import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const SETTINGS = {
  provider: "openrouter", key_configured: true, key_env_var: "OPENROUTER_API_KEY", model: "m/a",
  models: [{ id: "m/a", label: "A" }, { id: "m/b", label: "B" }],
  monthly_budget_inr: 300, usd_to_inr: 90, tracing_enabled: false,
};
const USAGE = { month: "2026-11", spent_inr: 1.5, budget_inr: 300, remaining_inr: 298.5, usd_to_inr: 90, disclaimer: "Costs are local estimates.", records: [] };
const SID = "a".repeat(32);

type Handler = (url: string, init?: RequestInit) => { status?: number; body: unknown } | undefined;
function mockFetch(handler: Handler) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const r = handler(url, init);
    if (!r) throw new Error(`unexpected fetch ${url}`);
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200 });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
const base: Handler = (url) => {
  if (url === "/api/sessions") return { body: { sessions: [] } };
  if (url === "/api/settings") return { body: SETTINGS };
  if (url === "/api/usage") return { body: USAGE };
};

beforeEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("renders sidebar and chat area", async () => {
    mockFetch(base);
    render(<App />);
    expect(screen.getByRole("navigation", { name: "Conversations" })).toBeInTheDocument();
    expect(screen.getByRole("log", { name: "Conversation" })).toBeInTheDocument();
    expect(await screen.findByText("No conversations yet.")).toBeInTheDocument();
  });

  it("sends a message and shows the reply with inspectable tool activity", async () => {
    const saved = {
      id: SID, title: "status", created_at: "2026-11-16T00:00:00+00:00", updated_at: "2026-11-16T00:00:00+00:00", message_count: 2,
      messages: [
        { id: 1, role: "user", content: "status of atlas?", tool_activity: [] },
        { id: 2, role: "assistant", content: "Atlas is on track.", tool_activity: [
          { id: "c1", name: "get_project_status", args: { project: "atlas" }, status: "ok", result: { project: { status: "on_track" } }, error: null }] },
      ],
    };
    let chatBody: unknown;
    mockFetch((url, init) => {
      if (url === "/api/chat") { chatBody = JSON.parse(String(init?.body)); return { body: { session_id: SID, request_id: "r", message: { role: "assistant", content: "x" }, tool_activity: [], usage: {} } }; }
      if (url === `/api/sessions/${SID}`) return { body: saved };
      return base(url, init);
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Message"), "status of atlas?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Atlas is on track.")).toBeInTheDocument();
    expect(chatBody).toEqual({ message: "status of atlas?", session_id: null });
    const tools = screen.getByRole("list", { name: "Tool activity" });
    expect(within(tools).getByText("get_project_status")).toBeInTheDocument();
    expect(within(tools).getByText("succeeded")).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

  it("shows the API error, keeps the draft and drops the unsaved message", async () => {
    mockFetch((url, init) => {
      if (url === "/api/chat") return { status: 402, body: { error: { code: "budget_exhausted", message: "The monthly model budget is used up." } } };
      return base(url, init);
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Message"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The monthly model budget is used up.");
    expect(screen.getByLabelText("Message")).toHaveValue("hello");
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("does not roll back a saved exchange when reloading it fails", async () => {
    mockFetch((url, init) => {
      if (url === "/api/chat") return { body: { session_id: SID, request_id: "r", message: { role: "assistant", content: "x" }, tool_activity: [], usage: {} } };
      if (url === `/api/sessions/${SID}`) return { status: 500, body: { error: { code: "internal_error", message: "boom" } } };
      return base(url, init);
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Message"), "keep me");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("sent and saved");
    expect(screen.getByText("keep me")).toBeInTheDocument(); // still shown, not rolled back
    expect(screen.getByLabelText("Message")).toHaveValue(""); // no invitation to resend (would be billed twice)
  });

  it("keeps focus in the settings field after a save re-render, and returns focus on close", async () => {
    mockFetch((url, init) => {
      if (url === "/api/settings" && init?.method === "PUT") return { body: { ...SETTINGS, model: "m/b" } };
      return base(url, init);
    });
    const user = userEvent.setup();
    render(<App />);
    const trigger = screen.getByRole("button", { name: "Settings" });
    await user.click(trigger);
    const dialog = await screen.findByRole("dialog");
    const select = within(dialog).getByLabelText("Tool-capable model");
    await user.selectOptions(select, "m/b");
    await waitFor(() => expect(select).toHaveValue("m/b"));
    expect(select).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
  });

  it("reports an unreachable backend", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("failed"); }));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the local backend");
  });

  it("reopens a saved conversation from the sidebar", async () => {
    const summary = { id: SID, title: "Old chat", created_at: "2026-11-15T00:00:00+00:00", updated_at: "2026-11-15T00:00:00+00:00", message_count: 2 };
    mockFetch((url, init) => {
      if (url === "/api/sessions") return { body: { sessions: [summary] } };
      if (url === `/api/sessions/${SID}`) return { body: { ...summary, messages: [
        { id: 1, role: "user", content: "earlier question", tool_activity: [] },
        { id: 2, role: "assistant", content: "earlier answer", tool_activity: [] }] } };
      return base(url, init);
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /Old chat/ }));
    expect(await screen.findByText("earlier answer")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Old chat/ })).toHaveAttribute("aria-current", "true");
  });

  it("send is disabled for blank input", async () => {
    mockFetch(base);
    const user = userEvent.setup();
    render(<App />);
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    await user.type(screen.getByLabelText("Message"), "   ");
    expect(send).toBeDisabled();
  });

  it("settings panel shows key presence only and saves a model change", async () => {
    const fn = mockFetch((url, init) => {
      if (url === "/api/settings" && init?.method === "PUT") return { body: { ...SETTINGS, model: "m/b" } };
      return base(url, init);
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("configured")).toBeInTheDocument();
    expect(within(dialog).queryByRole("textbox", { name: /key/i })).not.toBeInTheDocument();
    expect(await within(dialog).findByText(/Costs are local estimates/)).toBeInTheDocument();
    await user.selectOptions(within(dialog).getByLabelText("Tool-capable model"), "m/b");
    await waitFor(() => expect(fn).toHaveBeenCalledWith("/api/settings", expect.objectContaining({ method: "PUT", body: JSON.stringify({ model: "m/b" }) })));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("warns when no key is configured", async () => {
    mockFetch((url, init) => (url === "/api/settings" ? { body: { ...SETTINGS, provider: "nim", key_env_var: "NVIDIA_API_KEY", key_configured: false } } : base(url, init)));
    render(<App />);
    expect(await screen.findByText(/No API key is configured/)).toBeInTheDocument();
    expect(screen.getByText("NVIDIA_API_KEY")).toBeInTheDocument();
  });
});
