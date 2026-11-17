import re

from playwright.sync_api import expect
import pytest


ATLAS = "What is the status of atlas?"
ECHO = "(demo model) I can look up sample project status or search sample notes. You said:"
CHAT_ROUTE = "**/api/chat"


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {**browser_type_launch_args, "chromium_sandbox": True}


@pytest.fixture(autouse=True)
def capture_public_browser_http(page, request):
    responses = []
    network_errors = []
    request.node.qa_browser_responses = responses
    request.node.qa_browser_network_errors = network_errors
    page.on("response", lambda response: responses.append(response) if "/api/" in response.url else None)
    page.on("requestfailed", lambda failed: network_errors.append(failed) if "/api/" in failed.url else None)


def open_ui(page, app):
    page.set_viewport_size({"width": 1280, "height": 800})
    response = page.goto(app.url)
    assert response.status == 200, "Build the frontend as described in docs/TESTING.md"
    return page


@pytest.fixture
def ui(page, app):
    return open_ui(page, app)


def navigation(page):
    return page.get_by_role("navigation", name="Conversations")


def log(page):
    return page.get_by_role("log", name="Conversation").or_(page.get_by_role("region", name="Conversation"))


def message_box(page):
    return page.get_by_role("textbox", name="Message", exact=True)


def send_button(page):
    return page.get_by_role("button", name="Send", exact=True)


def row(page, title):
    return navigation(page).get_by_role("button", name=re.compile(r"^" + re.escape(title) + r"\s+\d+ messages"))


def options(page, title):
    return navigation(page).get_by_role("button", name=f"Options for {title}", exact=True)


def tool_list(page):
    return log(page).get_by_role("list", name="Tool activity")


def settings_button(page):
    return page.get_by_role("button", name="Settings", exact=True)


def settings_dialog(page):
    return page.get_by_role("dialog", name="Settings and usage")


def delete_dialog(page, title):
    row(page, title).hover()
    options(page, title).click()
    page.get_by_role("menuitem", name="Delete", exact=True).click()
    return page.get_by_role("alertdialog", name="Delete this chat?")


def is_chat(request):
    return request.method == "POST" and request.url.endswith("/api/chat")


def send(page, text, *, enter=False):
    message_box(page).fill(text)
    with page.expect_response(lambda response: is_chat(response.request)) as result:
        if enter:
            message_box(page).press("Enter")
        else:
            send_button(page).click()
    response = result.value
    assert response.ok
    return response.json()


def expect_exchange(page, text, answer):
    expect(log(page).get_by_text(text, exact=True)).to_be_visible()
    expect(log(page).get_by_text(answer, exact=True)).to_be_visible()


def hold_chat(page):
    pending = []
    page.route(CHAT_ROUTE, lambda route: pending.append(route))
    return pending


def test_UI_AC1_empty_desktop_layout(ui):
    expect(ui).to_have_title("chotuBot")
    expect(navigation(ui)).to_be_visible()
    expect(log(ui)).to_be_visible()
    expect(message_box(ui)).to_be_visible()
    expect(send_button(ui)).to_be_visible()
    expect(settings_button(ui)).to_be_visible()
    expect(navigation(ui).get_by_text("No conversations yet.", exact=True)).to_be_visible()


def test_UI_AC2_send_requires_non_whitespace(ui):
    expect(send_button(ui)).to_be_disabled()
    message_box(ui).fill("   ")
    expect(send_button(ui)).to_be_disabled()
    message_box(ui).fill("hello")
    expect(send_button(ui)).to_be_enabled()


@pytest.mark.parametrize("enter", [False, True], ids=["click", "enter"])
def test_UI_AC3_send_creates_exchange_and_sidebar_entry(ui, enter):
    chat = send(ui, ATLAS, enter=enter)
    expect_exchange(ui, ATLAS, chat["message"]["content"])
    expect(message_box(ui)).to_have_value("")
    expect(row(ui, ATLAS)).to_contain_text("2 messages")


def test_UI_AC4_shift_enter_inserts_a_line_without_sending(ui, app):
    box = message_box(ui)
    box.fill("hello")
    box.press("End")
    box.press("Shift+Enter")
    expect(box).to_have_value("hello\n")
    assert app.sessions() == []


def test_UI_AC5_expands_tool_arguments_and_result(ui):
    send(ui, ATLAS)
    activity = tool_list(ui)
    expect(activity).to_be_visible()
    expect(activity.get_by_text("succeeded", exact=True)).to_be_visible()
    expect(activity.get_by_text(re.compile(r'"progress_percent"\s*:\s*72'))).to_be_hidden()
    activity.get_by_text("get_project_status", exact=True).click()
    expect(activity.get_by_text(re.compile(r'"project"\s*:\s*"atlas"'))).to_be_visible()
    expect(activity.get_by_text(re.compile(r'"progress_percent"\s*:\s*72'))).to_be_visible()


def test_UI_AC6_plain_answer_has_no_tool_activity(ui):
    chat = send(ui, "hello")
    expect_exchange(ui, "hello", chat["message"]["content"])
    expect(tool_list(ui)).to_have_count(0)


def test_UI_AC7_renders_markdown_for_assistant_only(ui):
    send(ui, "**not bold**")
    expect(log(ui).get_by_text("**not bold**", exact=True)).to_be_visible()
    bold = log(ui).locator("strong, b").filter(has_text=re.compile(r"^not bold$"))
    expect(bold).to_be_visible()
    expect(log(ui).get_by_text(f"{ECHO} **not bold**", exact=True)).to_have_count(0)


@pytest.mark.parametrize("html", ["<img src=x onerror=alert(1)>", "<script>alert(1)</script>"])
def test_UI_AC8_does_not_render_raw_html_or_run_script(ui, html):
    dialogs = []

    def dismiss(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    ui.on("dialog", dismiss)
    send(ui, html)
    expect(log(ui).get_by_text(ECHO, exact=False)).to_be_visible()
    expect(log(ui).locator("img, script")).to_have_count(0)
    assert not dialogs


def test_UI_AC9_unsafe_link_is_sanitized_and_https_link_is_safe(ui):
    send(ui, "[unsafe](javascript:alert%281%29) [safe](https://example.com/)")
    safe = log(ui).get_by_role("link", name="safe", exact=True)
    expect(safe).to_have_attribute("href", "https://example.com/")
    expect(safe).to_have_attribute("target", "_blank")
    expect(safe).to_have_attribute("rel", re.compile(r"\bnoopener\b"))
    unsafe = log(ui).get_by_role("link", name="unsafe", exact=True)
    if unsafe.count():
        target = unsafe.get_attribute("href") or ""
        assert not target.strip().lower().startswith("javascript:")


def test_UI_AC10_history_and_tool_details_survive_restart(ui, app):
    old = send(ui, ATLAS)
    second = send(ui, "second greeting")
    app.restart()
    ui.reload()
    row(ui, ATLAS).click()
    expect_exchange(ui, ATLAS, old["message"]["content"])
    expect_exchange(ui, "second greeting", second["message"]["content"])
    expect(tool_list(ui).get_by_text("get_project_status", exact=True)).to_be_visible()
    followup = send(ui, "third greeting")
    assert followup["session_id"] == old["session_id"]
    expect(row(ui, ATLAS)).to_contain_text("6 messages")


def test_UI_AC11_new_chat_resets_view_and_creates_another_session(ui):
    first = send(ui, "first greeting")
    navigation(ui).get_by_role("button", name="New chat", exact=True).click()
    expect(log(ui).get_by_text("first greeting", exact=True)).to_have_count(0)
    expect(log(ui).get_by_text(re.compile(r"Try.*What is the status of atlas\?"))).to_be_visible()
    expect(ui.get_by_role("main").get_by_text("New conversation", exact=True)).to_be_visible()
    second = send(ui, "second greeting")
    assert second["session_id"] != first["session_id"]
    expect(row(ui, "first greeting")).to_be_visible()
    expect(row(ui, "second greeting")).to_be_visible()


def test_UI_AC12_only_active_row_has_aria_current(app, page):
    app.chat("first greeting")
    app.chat("second greeting")
    open_ui(page, app)
    row(page, "first greeting").click()
    expect(row(page, "first greeting")).to_have_attribute("aria-current", "true")
    row(page, "second greeting").click()
    expect(row(page, "second greeting")).to_have_attribute("aria-current", "true")
    expect(row(page, "first greeting")).not_to_have_attribute("aria-current", "true")


def test_UI_AC13_delete_opens_confirmation_focused_on_cancel(ui):
    send(ui, "hello")
    dialog = delete_dialog(ui, "hello")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text(re.compile(r"Are you sure you want to delete.*hello"))).to_be_visible()
    expect(dialog).to_contain_text(re.compile(r"permanently removed.*history.*memory"))
    expect(dialog).to_contain_text(re.compile(r"(?:cannot|can.t) be undone"))
    expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()


@pytest.mark.parametrize("escape", [False, True], ids=["cancel", "escape"])
def test_UI_AC14_dismissing_delete_keeps_conversation(ui, escape):
    send(ui, "hello")
    dialog = delete_dialog(ui, "hello")
    if escape:
        ui.keyboard.press("Escape")
    else:
        dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    expect(row(ui, "hello")).to_be_visible()
    ui.reload()
    expect(row(ui, "hello")).to_be_visible()


def test_UI_AC15_confirmed_delete_clears_open_chat_and_persists(ui):
    send(ui, "hello")
    dialog = delete_dialog(ui, "hello")
    dialog.get_by_role("button", name="Delete", exact=True).click()
    expect(dialog).to_be_hidden()
    expect(row(ui, "hello")).to_have_count(0)
    expect(log(ui).get_by_text("hello", exact=True)).to_have_count(0)
    ui.reload()
    expect(navigation(ui).get_by_text("No conversations yet.", exact=True)).to_be_visible()


def test_UI_AC16_delete_dialog_keeps_keyboard_out_of_background(ui):
    send(ui, "hello")
    dialog = delete_dialog(ui, "hello")
    expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
    for key in ["Tab"] * 6 + ["Shift+Tab"] * 6:
        ui.keyboard.press(key)
        assert dialog.evaluate("""(element) => {
            const active = document.activeElement;
            return active === document.body || element.contains(active);
        }""")


@pytest.mark.parametrize("escape", [False, True], ids=["close", "escape"])
def test_UI_AC17_settings_focus_and_dismissal(ui, escape):
    settings_button(ui).click()
    dialog = settings_dialog(ui)
    expect(dialog).to_be_visible()
    assert dialog.evaluate("(element) => element.contains(document.activeElement)")
    if escape:
        ui.keyboard.press("Escape")
    else:
        dialog.get_by_role("button", name="Close", exact=True).click()
    expect(dialog).to_be_hidden()
    expect(settings_button(ui)).to_be_focused()


@pytest.mark.parametrize("configured", [True, False], ids=["configured", "blank"])
def test_UI_AC18_settings_matches_api_and_omits_key_input(page, make_app, configured):
    app = make_app() if configured else make_app(key="")
    app.chat()
    app.chat("What is the status of atlas?")
    settings = app.settings()
    totals = app.usage()["totals"]
    open_ui(page, app)
    settings_button(page).click()
    dialog = settings_dialog(page)
    expect(dialog.get_by_role("heading", name=f"API key ({settings['provider']})")).to_be_visible()
    expect(dialog.get_by_text("configured" if settings["key_configured"] else "not configured",
                              exact=True)).to_be_visible()
    expect(dialog.get_by_role("combobox", name="Model")).to_have_value(settings["model"])
    expect(dialog.get_by_text(
        f"{totals['requests']:,} requests, {totals['total_tokens']:,} tokens, "
        f"average {totals['avg_latency_ms']:,} ms.", exact=True
    )).to_be_visible()
    expect(dialog.get_by_text("Tool-capable model", exact=False)).to_have_count(0)
    expect(dialog.get_by_text(re.compile(r"NVIDIA_API_KEY|\.env|read from"))).to_have_count(0)
    expect(dialog.get_by_role("textbox")).to_have_count(0)
    expect(dialog.locator("input[type=password]")).to_have_count(0)


def test_UI_AC19_model_change_saves_and_retains_dropdown_focus(ui, app):
    other = app.other_model()
    settings_button(ui).click()
    dropdown = settings_dialog(ui).get_by_role("combobox", name="Model")
    dropdown.focus()
    with ui.expect_response(lambda response: response.request.method == "PUT"
                            and response.url.endswith("/api/settings")) as saved:
        dropdown.select_option(value=other)
    assert saved.value.ok
    expect(dropdown).to_be_focused()
    assert app.settings()["model"] == other
    ui.reload()
    settings_button(ui).click()
    expect(settings_dialog(ui).get_by_role("combobox", name="Model")).to_have_value(other)


def test_UI_AC20_missing_key_notice_and_failed_send_restores_draft(page, make_app):
    app = make_app("nim", "")
    open_ui(page, app)
    expect(page.get_by_text(re.compile(r"^No API key is configured on the backend"))).to_be_visible()
    box = message_box(page)
    box.fill("hello draft")
    with page.expect_response(lambda response: is_chat(response.request)) as result:
        send_button(page).click()
    assert result.value.status == 503
    error = result.value.json()["error"]
    assert error["code"] == "missing_api_key"
    expect(page.get_by_role("alert")).to_contain_text(error["message"])
    expect(box).to_have_value("hello draft")
    expect(log(page).get_by_text("hello draft", exact=True)).to_have_count(0)
    assert app.sessions() == []


def test_UI_AC21_unreachable_action_shows_alert(ui, app):
    expect(navigation(ui).get_by_text("No conversations yet.", exact=True)).to_be_visible()
    app.stop()
    message_box(ui).fill("hello")
    send_button(ui).click()
    expect(ui.get_by_role("alert")).to_contain_text("Cannot reach the local backend. Is it running?")


def test_UI_AC22_mobile_sidebar_closes_and_stops_receiving_focus(page, app):
    app.chat("hello")
    page.set_viewport_size({"width": 360, "height": 740})
    assert page.goto(app.url).ok
    expect(navigation(page)).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    page.get_by_role("button", name="Open conversations").click()
    expect(navigation(page)).to_be_visible()
    row(page, "hello").click()
    expect(navigation(page)).to_be_hidden()
    message_box(page).focus()
    for key in ["Tab"] * 8 + ["Shift+Tab"] * 8:
        page.keyboard.press(key)
        assert page.evaluate("!document.activeElement?.closest('nav, [role=navigation]')")


def test_UI_AC23_message_box_caps_typed_input_at_4000(ui):
    text = "x" * 4000
    box = message_box(ui)
    box.fill(text[:-1])
    box.press("End")
    box.press_sequentially("xx")
    expect(box).to_have_value(text)
    with ui.expect_response(lambda response: is_chat(response.request)) as result:
        send_button(ui).click()
    assert result.value.ok
    assert result.value.request.post_data_json["message"] == text
    expect(box).to_have_value("")


def test_UI_AC24_pending_reply_disables_send_and_shows_status(ui):
    pending = hold_chat(ui)
    message_box(ui).fill("hello")
    with ui.expect_request(is_chat):
        send_button(ui).click()
    thinking = ui.get_by_role("status").filter(has_text="Thinking…")
    sending = ui.get_by_role("button", name="Sending…", exact=True)
    expect(sending).to_be_disabled()
    expect(thinking).to_be_visible()
    [route] = pending
    with ui.expect_response(lambda response: is_chat(response.request)) as result:
        route.continue_()
    expect_exchange(ui, "hello", result.value.json()["message"]["content"])
    expect(sending).to_have_count(0)
    expect(thinking).to_be_hidden()


def test_UI_AC25_initial_page_has_no_browser_errors(page, app):
    errors = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    with page.expect_response(lambda response: response.url.endswith("/api/settings")):
        open_ui(page, app)
    expect(navigation(page).get_by_text("No conversations yet.", exact=True)).to_be_visible()
    assert not errors


def test_UI_EDGE1_double_click_sends_once(ui, app):
    """Edge cases and failure modes: rapid double-click."""
    pending = hold_chat(ui)
    message_box(ui).fill("hello")
    with ui.expect_request(is_chat):
        send_button(ui).dblclick()
    expect(ui.get_by_role("button", name="Sending…", exact=True)).to_be_disabled()
    [route] = pending
    with ui.expect_response(lambda response: is_chat(response.request)):
        route.continue_()
    expect(row(ui, "hello")).to_contain_text("2 messages")
    assert len(pending) == app.usage()["totals"]["requests"] == 1


def test_UI_EDGE2_pending_reply_stays_with_original_conversation(page, app):
    """Edge cases and failure modes: switch while a reply is pending."""
    first = app.chat("first greeting")["session_id"]
    app.chat("second greeting")
    open_ui(page, app)
    row(page, "first greeting").click()
    expect(log(page).get_by_text("first greeting", exact=True)).to_be_visible()
    pending = hold_chat(page)
    text = "delayed greeting"
    message_box(page).fill(text)
    with page.expect_request(is_chat):
        send_button(page).click()
    row(page, "second greeting").click()
    expect(log(page).get_by_text("second greeting", exact=True)).to_be_visible()
    [route] = pending
    with page.expect_response(lambda response: is_chat(response.request)) as result:
        route.continue_()
    response = result.value.json()
    assert response["session_id"] == first
    assert app.session(first)["messages"][-1]["content"] == response["message"]["content"]
    expect(row(page, "first greeting")).to_contain_text("4 messages")
    expect(row(page, "second greeting")).to_have_attribute("aria-current", "true")
    expect(log(page).get_by_text(text, exact=True)).to_have_count(0)
    expect(log(page).get_by_text(response["message"]["content"], exact=True)).to_have_count(0)
    row(page, "first greeting").click()
    expect_exchange(page, text, response["message"]["content"])


@pytest.mark.parametrize(("text", "element"), [
    ("x" * 1000, None),
    ("\n\n|" + "|".join(f"col{i}" for i in range(1, 15)) + "|\n|" + "-|" * 14
     + "\n|" + "x|" * 14, "table"),
    ("\n\n~~~\n" + "x" * 160 + "\n~~~", "pre"),
], ids=["unbroken", "table", "code"])
def test_UI_EDGE3_wide_content_fits_viewport(ui, text, element):
    """Edge cases and failure modes: wide content does not scroll the page."""
    ui.set_viewport_size({"width": 360, "height": 740})
    send(ui, "hello" + text)
    expect(log(ui).get_by_text(ECHO, exact=False)).to_be_visible()
    assert ui.evaluate("document.documentElement.scrollWidth === innerWidth")
    if element:
        block = log(ui).locator(element)
        expect(block).to_be_visible()
        sizes = block.evaluate("""element => {
            const sizes = [];
            const article = element.closest("article");
            for (let node = element; node && node !== article?.parentElement; node = node.parentElement) {
                sizes.push({
                    element: node.tagName,
                    overflow: getComputedStyle(node).overflowX,
                    whiteSpace: getComputedStyle(node).whiteSpace,
                    contentWidth: node.scrollWidth,
                    viewportWidth: node.clientWidth
                });
                if (node === document.body) break;
            }
            return sizes;
        }""")
        assert any(size["overflow"] in {"auto", "scroll"} and size["contentWidth"] > size["viewportWidth"]
                   for size in sizes), sizes


def test_UI_EDGE4_failed_delete_keeps_dialog_and_conversation(ui, app):
    """Edge cases and failure modes: server error during deletion."""
    chat = send(ui, "hello")
    path = f"/api/sessions/{chat['session_id']}"
    error = {"error": {"code": "internal_error", "message": "An internal error occurred."}}

    def fail_delete(route):
        if route.request.method == "DELETE":
            route.fulfill(status=500, json=error)
        else:
            route.continue_()

    ui.route(f"**{path}", fail_delete)
    dialog = delete_dialog(ui, "hello")
    with ui.expect_response(lambda response: response.request.method == "DELETE"
                            and response.url.endswith(path)) as result:
        dialog.get_by_role("button", name="Delete", exact=True).click()
    assert result.value.status == 500
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text(error["error"]["message"], exact=False)).to_be_visible()
    assert app.session(chat["session_id"])["message_count"] == 2


def test_UI_EDGE5_inactive_options_remain_keyboard_focusable(ui, app):
    """Edge cases and failure modes: transparent options become opaque on hover or focus."""
    app.chat("second greeting")
    app.chat("first greeting")
    ui.reload()
    row(ui, "first greeting").click()
    message_box(ui).hover()
    message_box(ui).focus()
    inactive = options(ui, "second greeting")
    expect(inactive).to_have_count(1)
    expect(inactive).to_have_css("opacity", "0")
    row(ui, "second greeting").hover()
    expect(inactive).to_have_css("opacity", "1")
    message_box(ui).hover()
    row(ui, "second greeting").focus()
    ui.keyboard.press("Tab")
    expect(inactive).to_be_focused()
    expect(inactive).to_have_css("opacity", "1")


def test_UI_EDGE5_touch_shows_inactive_options_without_hover(browser, app):
    """Edge cases and failure modes: touch shows options without hover."""
    app.chat("touch greeting")
    page = browser.new_page(viewport={"width": 360, "height": 740}, has_touch=True, is_mobile=True)
    try:
        assert page.goto(app.url).ok
        page.get_by_role("button", name="Open conversations").tap()
        button = options(page, "touch greeting")
        expect(button).to_be_visible()
        expect(button).not_to_have_css("opacity", "0")
    finally:
        page.close()


def test_UI_EDGE6_dark_mode_follows_browser_setting(ui):
    """Edge cases and failure modes: browser preference updates the theme."""
    def colors():
        return message_box(ui).evaluate("""element => ({
            text: getComputedStyle(element).color,
            background: getComputedStyle(element).backgroundColor
        })""")

    ui.emulate_media(color_scheme="light")
    light = colors()
    ui.emulate_media(color_scheme="dark")
    dark = colors()
    assert dark != light
    assert dark["text"] != dark["background"]
    expect(message_box(ui)).to_be_visible()
