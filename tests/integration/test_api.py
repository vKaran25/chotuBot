import socket

import pytest


def test_API_AC1_health(app):
    data = app.get("/api/health")
    assert data["status"] == "ok"
    assert isinstance(data["version"], str) and data["version"].strip()


def test_API_AC2_json_error_envelope(app):
    responses = [
        app.client.get("/api/nope"),
        app.client.put("/api/health"),
        app.client.post("/api/chat", json={}),
    ]
    for response in responses:
        assert response.is_error
        assert response.headers["content-type"].startswith("application/json")
        data = response.json()
        assert set(data) == {"error"}
        assert all(isinstance(data["error"][key], str) for key in ("code", "message"))


def test_API_AC3_validation_details(app):
    error = app.error(app.client.post("/api/chat", json={}), 422, "invalid_request")
    assert isinstance(error["details"], list) and error["details"]
    for detail in error["details"]:
        assert all(isinstance(detail[key], str) and detail[key] for key in ("field", "reason"))


def test_API_AC4_validation_omits_rejected_value(app):
    marker = "MARKER-XYZ"
    response = app.client.post("/api/chat", json={"message": marker + "x" * 4990})
    app.error(response, 422, "invalid_request")
    assert marker not in response.text


def test_API_AC5_unknown_api_path(app):
    app.error(app.client.get("/api/nope"), 404, "not_found")


def test_API_AC6_unsupported_method(app):
    app.error(app.client.put("/api/health"), 405, "http_error")


def test_API_AC7_forbidden_host_does_not_mutate(app):
    before = app.state()
    response = app.client.post("/api/sessions", json={}, headers={"Host": "evil.example.com"})
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/plain")
    assert app.state() == before


def test_API_AC8_loopback_hosts(app):
    for host in ("localhost", "127.0.0.1"):
        response = app.client.get("/api/health", headers={"Host": f"{host}:{app.port}"})
        assert app.json(response)["status"] == "ok"


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost.evil.example"])
def test_API_AC9_rejects_cross_site_mutations(app, origin):
    session = app.create()
    before = app.state()
    headers = {"Origin": origin}
    for method, path, kwargs in (
        ("POST", "/api/sessions", {"json": {}}),
        ("PUT", "/api/settings", {"json": {"model": before[0]["model"]}}),
        ("PATCH", "/api/settings", {"json": {"model": before[0]["model"]}}),
        ("DELETE", f"/api/sessions/{session['id']}", {}),
    ):
        app.error(app.client.request(method, path, headers=headers, **kwargs), 403, "forbidden_origin")
        assert app.state() == before


def test_API_AC10_accepts_loopback_origins_on_other_ports(app):
    for host in ("localhost", "127.0.0.1"):
        other_port = 5173 if app.port != 5173 else 5174
        response = app.client.post("/api/sessions", json={}, headers={"Origin": f"http://{host}:{other_port}"})
        assert app.json(response, 201)["message_count"] == 0


def test_API_AC11_fetch_site_protection(app):
    before = app.state()
    response = app.client.post("/api/sessions", json={}, headers={"Sec-Fetch-Site": "cross-site"})
    app.error(response, 403, "forbidden_origin")
    assert app.state() == before
    response = app.client.post("/api/sessions", json={}, headers={"Sec-Fetch-Site": "same-origin"})
    assert app.json(response, 201)["message_count"] == 0


@pytest.mark.parametrize("headers", [{}, {"Content-Type": "text/plain"}], ids=["missing", "plain"])
def test_API_AC12_body_requires_json(app, headers):
    before = app.state()
    for method, path in (("POST", "/api/sessions"), ("PUT", "/api/settings")):
        response = app.client.request(method, path, content=b"{}", headers=headers)
        app.error(response, 415, "unsupported_media_type")
        assert app.state() == before


def test_API_AC13_rejects_oversized_json(app):
    before = app.state()
    response = app.client.post("/api/chat", json={"message": "x" * 65536})
    app.error(response, 413, "payload_too_large")
    assert app.state() == before


def test_API_AC14_accepts_only_loopback_connections(app):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))  # Selects the local interface without sending a datagram.
        except OSError:
            pytest.skip("No routed IPv4 interface for the LAN connection check")
        lan_ip = sock.getsockname()[0]
    if lan_ip.startswith("127.") or lan_ip == "0.0.0.0":
        pytest.skip("No non-loopback IPv4 address available")
    with pytest.raises((ConnectionRefusedError, TimeoutError)):
        with socket.create_connection((lan_ip, app.port), timeout=1):
            pass
    assert app.get("/api/health")["status"] == "ok"


def test_API_AC15_gets_leave_stored_state_unchanged(app):
    session_id = app.chat()["session_id"]
    paths = ["/api/health", "/api/settings", "/api/sessions", f"/api/sessions/{session_id}", "/api/usage"]
    before = {path: app.get(path) for path in paths}
    assert {path: app.get(path) for path in reversed(paths)} == before


def test_API_EDGE1_guard_order(app):
    """Edge cases and failure modes: origin, media type, size, validation."""
    before = app.state()
    body = b'{"message":"' + b"x" * 65536 + b'"}'
    for headers, content, status, code in (
        ({"Origin": "https://evil.example", "Content-Type": "text/plain"}, body, 403, "forbidden_origin"),
        ({"Content-Type": "text/plain"}, body, 415, "unsupported_media_type"),
        ({"Content-Type": "application/json"}, body, 413, "payload_too_large"),
        ({"Content-Type": "application/json"}, b"{}", 422, "invalid_request"),
    ):
        app.error(app.client.post("/api/chat", content=content, headers=headers), status, code)
    assert app.state() == before


def test_API_EDGE2_get_ignores_origin(app):
    """Edge cases and failure modes: Origin on GET."""
    response = app.client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert app.json(response)["status"] == "ok"


def test_API_EDGE3_same_origin_headers_are_allowed(app):
    """Edge cases and failure modes: matching Origin and fetch site."""
    response = app.client.post(
        "/api/sessions", json={}, headers={"Origin": app.url, "Sec-Fetch-Site": "same-origin"}
    )
    assert response.status_code == 201


def test_API_EDGE4_delete_with_empty_body(app):
    """Edge cases and failure modes: empty DELETE."""
    session_id = app.create()["id"]
    response = app.client.delete(f"/api/sessions/{session_id}")
    assert response.status_code == 204 and not response.content


def test_API_EDGE5_api_404_and_405_use_documented_error_shape(app):
    """Edge cases and failure modes: JSON routing is scoped to /api."""
    app.error(app.client.get("/api/nope"), 404, "not_found")
    app.error(app.client.put("/api/health"), 405, "http_error")
