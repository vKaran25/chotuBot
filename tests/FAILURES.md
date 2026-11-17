# Resolved application defects

All four reported test cases passed on the rebuilt app after the fixes. The exchanges below were captured before the fixes.

## SESS-AC6 — An updated conversation stays at the bottom of the list

Test `test_SESS_AC6_orders_by_most_recent_update`.

The spec requires `greeting 0` to move to the top of `GET /api/sessions` after a successful follow-up.

The follow-up succeeded and the message count went from 2 to 4. The list still returned `greeting 2`, `greeting 1`, `greeting 0`. All three entries exposed the same `updated_at` value.

The following exchanges were captured on loopback port `53208` after creating conversations with `greeting 0`, `greeting 1` and `greeting 2`.

```http
POST /api/chat HTTP/1.1
Host: 127.0.0.1:53208
Content-Type: application/json

{"message":"new turn","session_id":"f13fb29b5ec44a5f931b7302971ccc78"}

HTTP/1.1 200 OK
{"session_id":"f13fb29b5ec44a5f931b7302971ccc78","request_id":"436539766d964d45bb8ac2fd58ce46e0","message":{"role":"assistant","content":"(demo model) I can look up sample project status or search sample notes. You said: new turn"},"tool_activity":[],"usage":{"model":"nvidia/nemotron-3.5-lightning-30b-a3b","input_tokens":186,"output_tokens":23,"total_tokens":209,"latency_ms":6,"tool_call_count":0}}
```

```http
GET /api/sessions HTTP/1.1
Host: 127.0.0.1:53208

HTTP/1.1 200 OK
{"sessions":[{"id":"28780b5d03e3442cb2c91ad299dfcd6e","title":"greeting 2","created_at":"2026-11-16T17:08:28+00:00","updated_at":"2026-11-16T17:08:28+00:00","message_count":2},{"id":"1d88ae272c414beeb2e05a497ec062f0","title":"greeting 1","created_at":"2026-11-16T17:08:28+00:00","updated_at":"2026-11-16T17:08:28+00:00","message_count":2},{"id":"f13fb29b5ec44a5f931b7302971ccc78","title":"greeting 0","created_at":"2026-11-16T17:08:28+00:00","updated_at":"2026-11-16T17:08:28+00:00","message_count":4}]}
```

## UI-AC17 — Focus stays off Settings after dismissal

Tests `test_UI_AC17_settings_focus_and_dismissal[chromium-close]` and `test_UI_AC17_settings_focus_and_dismissal[chromium-escape]`.

The spec requires **Settings** to open **Settings and usage** with focus inside. Clicking **Close** or pressing Escape dismisses it and returns focus to **Settings**.

Focus moved into the open dialog and both dismissal actions closed it. The **Settings** button was still unfocused five seconds later for both actions. Dismissal is a browser action with no HTTP request.

These were the browser's API requests and response bodies when opening settings. Both runs returned identical bodies. The **Close** run used port `55422`, and the **Escape** run used port `55501`.

```http
GET /api/settings HTTP/1.1
Host: 127.0.0.1:55422

HTTP/1.1 200 OK
{"provider":"demo","key_configured":true,"key_env_var":"NVIDIA_API_KEY","model":"nvidia/nemotron-3.5-lightning-30b-a3b","models":[{"id":"openai/gpt-oss-20b","label":"GPT-OSS 20B (NIM, timed out in testing)","tool_capable":true},{"id":"nvidia/nemotron-3.5-lightning-30b-a3b","label":"Nemotron 3.5 Lightning 30B-A3B (NIM, fast - default for NIM)","tool_capable":true}],"tracing_enabled":false}
```

```http
GET /api/usage HTTP/1.1
Host: 127.0.0.1:55422

HTTP/1.1 200 OK
{"totals":{"requests":0,"total_tokens":0,"avg_latency_ms":0},"records":[]}
```

## UI Edge case 3 — A wide code block wraps its long line

Test `test_UI_EDGE3_wide_content_fits_viewport[chromium-code]`.

At 360px width, the spec requires the message's wide code block to scroll horizontally within its own block while the page fits the viewport.

The page fit the viewport. Chromium rendered the single long line with `white-space: pre-wrap`, and the code block had no horizontal scrolling range.

```http
POST /api/chat HTTP/1.1
Host: 127.0.0.1:57409
Content-Type: application/json

{"message":"hello\n\n~~~\nxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n~~~","session_id":null}

HTTP/1.1 200 OK
{"session_id":"9aae547d22794acf8cdb8ec7d48a067b","request_id":"6cd7e012d30d46ad9f3a273416b90099","message":{"role":"assistant","content":"(demo model) I can look up sample project status or search sample notes. You said: hello\n\n~~~\nxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n~~~"},"tool_activity":[],"usage":{"model":"nvidia/nemotron-3.5-lightning-30b-a3b","input_tokens":202,"output_tokens":65,"total_tokens":267,"latency_ms":14,"tool_call_count":0}}
```
