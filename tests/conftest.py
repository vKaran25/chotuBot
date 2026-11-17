import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
DUMMY_KEY = "nvapi-DUMMY-TEST-KEY-0000000000"
INVALID_KEY = "nvapi-INVALID-TEST-KEY-000000"
DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"


def redact(text):
    for key in (DUMMY_KEY, INVALID_KEY):
        for length in range(len(key), 8, -1):
            for index in range(len(key) - length + 1):
                text = text.replace(key[index:index + length], "<credential redacted>")
    return text


class App:
    def __init__(self, directory, provider, key, trace):
        self.directory = directory
        self.db = directory / "qa.sqlite3"
        self.credentials = {"CHOTU_PROVIDER": provider, "NVIDIA_API_KEY": key}
        self.trace = trace
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.client = httpx.Client(
            base_url=self.url, timeout=90, trust_env=False,
            event_hooks={"response": [self.record]},
        )
        self.process = None

    def record(self, response):
        request = response.request
        response.read()
        headers = {
            name: value for name, value in request.headers.items()
            if name in {"host", "origin", "sec-fetch-site", "content-type"}
        }
        body = request.content.decode(errors="replace")
        self.trace.append(redact(
            f"{request.method} {request.url} {headers}\n{body}\n"
            f"HTTP {response.status_code} {response.text}"
        ))

    def start(self):
        env = dict(os.environ, **self.credentials)
        env.update(
            CHOTU_PORT=str(self.port), CHOTU_DB_PATH=str(self.db),
            CHOTU_DEFAULT_MODEL=DEFAULT_MODEL, CHOTU_LANGSMITH_ENABLED="false",
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.process = subprocess.Popen(
            [sys.executable, "-m", "app"], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
        while self.process.poll() is None and time.monotonic() < deadline:
            try:
                response = self.client.get("/api/health", timeout=0.5)
                if response.status_code == 200 and response.json().get("status") == "ok":
                    return self
            except (httpx.RequestError, ValueError):
                pass
            time.sleep(0.1)
        exit_code = self.process.poll()
        self.stop()
        if exit_code is None:
            pytest.fail(f"No healthy /api/health response from {self.url} within 30 seconds")
        pytest.fail(f"python -m app exited with code {exit_code} before /api/health was ready")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def restart(self):
        self.stop()
        self.start()

    def get(self, path):
        return self.json(self.client.get(path))

    @staticmethod
    def json(response, status=200):
        assert response.status_code == status, f"Expected HTTP {status}, got {response.status_code}"
        return response.json()

    @staticmethod
    def error(response, status, code):
        data = App.json(response, status)
        assert set(data) == {"error"}
        error = data["error"]
        assert isinstance(error["message"], str)
        assert error["code"] == code
        return error

    def chat(self, message="hello", **fields):
        return self.json(self.client.post("/api/chat", json={"message": message, **fields}))

    def create(self, **fields):
        return self.json(self.client.post("/api/sessions", json=fields), 201)

    def sessions(self):
        return self.get("/api/sessions")["sessions"]

    def session(self, session_id):
        return self.get(f"/api/sessions/{session_id}")

    def usage(self):
        return self.get("/api/usage")

    def settings(self):
        return self.get("/api/settings")

    def select_model(self, model):
        return self.json(self.client.put("/api/settings", json={"model": model}))

    def other_model(self):
        settings = self.settings()
        options = [model["id"] for model in settings["models"] if model["id"] != settings["model"]]
        if not options:
            pytest.skip("This scenario requires a second allowed model")
        return options[0]

    def state(self):
        return self.settings(), self.sessions(), self.usage()


@pytest.fixture
def make_app(tmp_path, request):
    apps = []
    trace = []
    request.node.qa_http_trace = trace

    def start(provider="demo", key=DUMMY_KEY):
        directory = tmp_path / str(len(apps))
        directory.mkdir()
        app = App(directory, provider, key, trace)
        apps.append(app)
        return app.start()

    yield start
    for app in reversed(apps):
        app.stop()
        app.client.close()


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def invalid_provider(make_app):
    return make_app("nim", INVALID_KEY)


@pytest.fixture(params=["missing", "invalid"])
def provider_failure(request, make_app):
    if request.param == "missing":
        return make_app("nim", ""), "missing_api_key", "missing_key"
    return make_app("nim", INVALID_KEY), "provider_unavailable", "provider_error"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.failed:
        trace = list(getattr(item, "qa_http_trace", []))
        for response in getattr(item, "qa_browser_responses", []):
            request = response.request
            try:
                body = response.text()
            except Exception:
                body = "<response body unavailable>"
            trace.append(redact(
                f"{request.method} {request.url}\n{request.post_data or ''}\n"
                f"HTTP {response.status} {body}"
            ))
        for request in getattr(item, "qa_browser_network_errors", []):
            trace.append(redact(
                f"{request.method} {request.url}\n{request.post_data or ''}\n"
                f"No HTTP response: {request.failure}"
            ))
        if trace:
            report.sections.append(("Public HTTP exchanges", "\n\n".join(trace)))
    return report
