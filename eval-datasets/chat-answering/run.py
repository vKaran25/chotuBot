"""Run approved chat cases through a running local chotuBot instance."""
import argparse
from collections import Counter
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from time import monotonic, sleep
from urllib.parse import urlsplit

import httpx


DATASET = Path(__file__).with_name("golden.jsonl")
TRANSIENT_STATUS = {429, 502, 503, 504}
NIM_TOKEN = re.compile(r"nvapi-[a-zA-Z0-9_-]+")
REPORT_FORMAT = 1
LIVE_COOLDOWN = 8


class RunError(RuntimeError):
    """An operational failure, separate from a model or tool result."""


def load_cases(path):
    data = path.read_bytes()
    cases = [json.loads(line) for line in data.decode("utf-8-sig").splitlines() if line.strip()]
    if not cases:
        raise ValueError("The approved dataset is empty.")
    ids = set()
    for case in cases:
        case_id = case["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", case_id) or case_id in ids:
            raise ValueError("Case IDs must be unique and contain only letters, numbers, _ or -.")
        ids.add(case_id)
        review = case["metadata"]
        if review["review_status"] != "approved" or not str(review["reviewed_by"] or "").strip():
            raise ValueError(f"{case_id} needs human approval.")
        date.fromisoformat(review["reviewed_at"])
        turns = case["input"]["turns"]
        if not isinstance(turns, list) or not turns or not all(
            isinstance(turn, str) and 0 < len(turn.strip()) <= 4000 for turn in turns
        ):
            raise ValueError(f"{case_id} needs valid chat turns.")
        if not (case["expected_output"] or case["pass_criteria"]):
            raise ValueError(f"{case_id} needs an answer rubric.")
        expected = case["expected_output"]
        if expected is not None and not isinstance(expected, dict):
            raise ValueError(f"{case_id} has an invalid expected_output.")
        if expected is not None and "tool_calls" in expected:
            calls = expected["tool_calls"]
            if not isinstance(calls, list) or not all(
                isinstance(call, dict) and isinstance(call.get("name"), str) for call in calls
            ):
                raise ValueError(f"{case_id} has invalid expected tool calls.")
    return cases, sha256(data).hexdigest()


def local_url(value):
    url = urlsplit(value)
    if (
        url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"}
        or url.username is not None or url.password is not None
        or url.path not in {"", "/"} or url.query or url.fragment
    ):
        raise ValueError("Use a local HTTP URL such as http://127.0.0.1:8765.")
    return f"http://{url.hostname}:{url.port or 80}"


def error_code(response):
    try:
        code = response.json()["error"]["code"]
    except (KeyError, TypeError, ValueError):
        return "unknown"
    return code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,60}", code) else "unknown"


def retry_after(response):
    value = response.headers.get("Retry-After")
    if value is None:
        return 0
    try:
        if value.isascii() and value.isdecimal():
            seconds = float(value)
            return seconds if math.isfinite(seconds) else 0
        return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 0


class ChatClient:
    def __init__(self, client, interval, retries):
        self.client = client
        self.interval = interval
        self.retries = retries
        self.next_start = monotonic()

    def send(self, message, session_id):
        body = {"message": message}
        if session_id is not None:
            body["session_id"] = session_id
        for attempt in range(self.retries + 1):
            sleep(max(0, self.next_start - monotonic()))
            try:
                response = self.client.post("/api/chat", json=body)
            except httpx.RequestError as exc:
                # The server may have saved the exchange before a connection or read failure.
                self.next_start = monotonic() + self.interval
                raise RunError(f"Chat transport error ({type(exc).__name__}).") from None
            self.next_start = monotonic() + self.interval
            if response.status_code == 200:
                return response.json()

            code = error_code(response)
            # Only retry failures known to have rejected the request or saved no exchange.
            retryable = response.status_code == 429 or (
                response.status_code in TRANSIENT_STATUS and code == "provider_unavailable"
            )
            if not retryable or attempt == self.retries:
                raise RunError(f"Chat failed with HTTP {response.status_code} ({code}).")
            delay = max(retry_after(response), 10 * 2**attempt)
            print(f"Waiting {delay:g}s after HTTP {response.status_code} ({code}).", flush=True)
            self.next_start = max(self.next_start, monotonic() + delay)


def score_tools(expected, actual):
    if expected is None:
        return "unchecked"
    if len(expected) != len(actual):
        return "fail"
    for wanted, got in zip(expected, actual):
        if any(got.get(key) != wanted[key] for key in ("name", "args", "status") if key in wanted):
            return "fail"
        if "result_ids" in wanted:
            result = got.get("result")
            notes = result.get("results") if isinstance(result, dict) else None
            if not isinstance(notes, list) or [note.get("id") for note in notes] != wanted["result_ids"]:
                return "fail"
    return "pass"


def run_case(chat, case, model):
    expected = case["expected_output"] or {}
    result = {
        "slice": case["metadata"]["slice"],
        "expected_output": case["expected_output"],
        "pass_criteria": case["pass_criteria"],
        "turns": [],
        "answer_grade": "pending",
    }
    session_id = None
    try:
        for message in case["input"]["turns"]:
            response = chat.send(message, session_id)
            received_id = response["session_id"]
            answer = response["message"]["content"]
            tool_activity = response["tool_activity"]
            if response["usage"]["model"] != model:
                raise RunError("The model changed during the run.")
            if (
                not isinstance(received_id, str) or not re.fullmatch(r"[0-9a-f]{32}", received_id)
                or (session_id is not None and session_id != received_id)
                or not isinstance(answer, str) or not answer.strip()
                or not isinstance(tool_activity, list) or not all(isinstance(tool, dict) for tool in tool_activity)
            ):
                raise RunError("Chat returned an invalid reply or session.")
            session_id = received_id
            result["turns"].append({
                "prompt": message,
                "answer": answer,
                "tool_activity": tool_activity,
                "request_id": response["request_id"],
            })
        result["tool_grade"] = score_tools(expected.get("tool_calls"), tool_activity)
    except (KeyError, TypeError, ValueError, AttributeError):
        result.update(status="error", error="Chat returned an invalid response.")
    except RunError as exc:
        result.update(status="error", error=str(exc))
    else:
        result["status"] = "completed"
    return result


def redact(value):
    if isinstance(value, str):
        return NIM_TOKEN.sub("[redacted]", value)
    if isinstance(value, dict):
        return {redact(key): redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def save_report(path, report, *, create=False):
    body = json.dumps(redact(report), ensure_ascii=False, indent=2) + "\n"
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as target:
            target.write(body)
        return
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as target:
        temporary = Path(target.name)
        target.write(body)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_report(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("format") != REPORT_FORMAT:
        raise ValueError("Unsupported evaluation report format.")
    return report


def run_cases(chat, cases, model, report, save):
    for case in cases:
        case_id = case["id"]
        previous = report["results"].get(case_id, {})
        if previous.get("status") == "completed":
            continue
        result = run_case(chat, case, model)
        errors = previous.get("previous_errors", [])
        if previous.get("status") == "error":
            errors = [*errors, previous["error"]]
        if errors:
            result["previous_errors"] = errors
        report["results"][case_id] = result
        save()
        print(f"{case_id} {result.get('tool_grade', result['status'])}", flush=True)
        if result["status"] == "error":
            print(result["error"], flush=True)
            return


def summarize(report):
    results = report["results"].values()
    completed = [item for item in results if item["status"] == "completed"]
    tools = Counter(item["tool_grade"] for item in completed)
    answers = Counter(item["answer_grade"] for item in completed)
    if not answers.keys() <= {"pass", "fail", "pending"}:
        raise ValueError("Answer grades must be pass, fail, or pending.")
    errors = len(results) - len(completed)
    pending = report["case_count"] - len(results)
    print(f"Completed {len(completed)}/{report['case_count']}. Operational errors {errors}. Unrun {pending}.")
    print(f"Tools: {tools['pass']} pass, {tools['fail']} fail, {tools['unchecked']} unchecked.")
    print(f"Answers: {answers['pass']} pass, {answers['fail']} fail, {answers['pending']} awaiting review.")
    if errors or pending:
        return 2
    return int(bool(tools["fail"] or answers["fail"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--interval", type=float, default=LIVE_COOLDOWN, help="Seconds between a reply and the next submission.")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--live", action="store_true", help="Use an app configured for NVIDIA NIM.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--summary", action="store_true", help="Read an existing report without making requests.")
    args = parser.parse_args()

    try:
        if args.summary:
            return summarize(load_report(args.out))
        if args.out.exists() != args.resume:
            raise ValueError("Use a new output path, or use --resume with the original report.")
        if not math.isfinite(args.interval) or args.interval < (LIVE_COOLDOWN if args.live else 0):
            raise ValueError(f"Set a finite interval of at least {LIVE_COOLDOWN} seconds for NIM, or 0 for demo.")
        if not 0 <= args.retries <= 5:
            raise ValueError("--retries must be between 0 and 5.")
        cases, dataset_hash = load_cases(args.dataset)
        snapshot = args.dataset.with_name("source-snapshot.json")
        target = {
            "base_url": local_url(args.base_url),
            "dataset_sha256": dataset_hash,
            "source_sha256": sha256(snapshot.read_bytes()).hexdigest(),
            "provider": "nim" if args.live else "demo",
        }
        with httpx.Client(base_url=target["base_url"], timeout=90, trust_env=False) as client:
            response = client.get("/api/settings")
            if response.status_code != 200:
                raise ValueError(f"Could not read local app settings (HTTP {response.status_code}).")
            settings = response.json()
            if settings["provider"] != target["provider"]:
                raise ValueError(f"This run requires an app configured for {target['provider']}.")
            if args.live and not settings["key_configured"]:
                raise ValueError("Configure the NVIDIA NIM key in the app first.")
            target["model"] = settings["model"]
            if args.resume:
                report = load_report(args.out)
                if report["target"] != target:
                    raise ValueError("The dataset, fixture, or app settings changed. Use a new report.")
            else:
                report = {
                    "format": REPORT_FORMAT,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "target": target,
                    "case_count": len(cases),
                    "results": {},
                }
                save_report(args.out, report, create=True)
            chat = ChatClient(client, args.interval, args.retries)
            if args.resume:
                chat.next_start += args.interval
            run_cases(chat, cases, target["model"], report, lambda: save_report(args.out, report))
        print(f"Report {args.out}")
        return summarize(report)
    except KeyboardInterrupt:
        print("Stopped. Use --resume to continue from the last saved case.")
        return 130
    except (OSError, ValueError, KeyError, TypeError, httpx.RequestError) as exc:
        print(f"Evaluation stopped: {redact(str(exc))}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
