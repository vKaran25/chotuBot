import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const SETTINGS = {
  provider: "nim", key_configured: true, key_env_var: "NVIDIA_API_KEY", model: "m/a",
  models: [
    { id: "m/a", label: "A", category: "fast" },
    { id: "m/b", label: "B", category: "thinking" },
    { id: "m/c", label: "C", category: "flagship" },
  ],
  tracing_enabled: false,
};
const USAGE = { totals: { requests: 3, total_tokens: 1200, avg_latency_ms: 800 }, records: [] };
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
  it("groups selectable models into Fast, Thinking, and Flagship", async () => {
    mockFetch(base);
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog");
    for (const [category, id] of [["Fast", "m/a"], ["Thinking", "m/b"], ["Flagship", "m/c"]]) {
      const group = within(dialog).getByRole("group", { name: category });
      expect(within(group).getByRole("option")).toHaveValue(id);
    }
  });

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
      if (url === "/api/chat") return { status: 503, body: { error: { code: "provider_unavailable", message: "The model provider is unavailable." } } };
      return base(url, init);
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Message"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The model provider is unavailable.");
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
    const select = within(dialog).getByLabelText("Model");
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
    await user.click(await screen.findByRole("button", { name: /^Old chat/ }));
    expect(await screen.findByText("earlier answer")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Old chat/ })).toHaveAttribute("aria-current", "true");
  });

  describe("deleting a chat", () => {
    const summary = { id: SID, title: "Old chat", created_at: "2026-11-15T00:00:00+00:00", updated_at: "2026-11-15T00:00:00+00:00", message_count: 2 };
    const detail = { ...summary, messages: [{ id: 1, role: "user", content: "earlier question", tool_activity: [] }, { id: 2, role: "assistant", content: "earlier answer", tool_activity: [] }] };

    function setup(deleteResult: { status: number; body: unknown } = { status: 204, body: null }) {
      let sessions = [summary];
      const fn = vi.fn(async (url: string, init?: RequestInit) => {
        if (url === "/api/sessions") return new Response(JSON.stringify({ sessions }), { status: 200 });
        if (url === `/api/sessions/${SID}` && init?.method === "DELETE") {
          if (deleteResult.status === 204) sessions = [];
          return new Response(deleteResult.status === 204 ? null : JSON.stringify(deleteResult.body), { status: deleteResult.status });
        }
        if (url === `/api/sessions/${SID}`) return new Response(JSON.stringify(detail), { status: 200 });
        if (url === "/api/settings") return new Response(JSON.stringify(SETTINGS), { status: 200 });
        if (url === "/api/usage") return new Response(JSON.stringify(USAGE), { status: 200 });
        throw new Error(`unexpected fetch ${url}`);
      });
      vi.stubGlobal("fetch", fn);
      return fn;
    }
    const deletes = (fn: ReturnType<typeof vi.fn>) => fn.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "DELETE");

    it("asks for confirmation, and cancelling deletes nothing", async () => {
      const fn = setup();
      const user = userEvent.setup();
      render(<App />);
      await user.click(await screen.findByRole("button", { name: "Options for Old chat" }));
      await user.click(screen.getByRole("menuitem", { name: "Delete" }));
      const dlg = await screen.findByRole("alertdialog");
      expect(dlg).toHaveTextContent("Are you sure you want to delete");
      expect(dlg).toHaveTextContent("assistant's memory");
      expect(within(dlg).getByRole("button", { name: "Cancel" })).toHaveFocus();
      await user.click(within(dlg).getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      expect(deletes(fn)).toHaveLength(0);
      expect(screen.getByRole("button", { name: /^Old chat/ })).toBeInTheDocument();
    });

    it("Escape also cancels", async () => {
      const fn = setup();
      const user = userEvent.setup();
      render(<App />);
      await user.click(await screen.findByRole("button", { name: "Options for Old chat" }));
      await user.click(screen.getByRole("menuitem", { name: "Delete" }));
      await screen.findByRole("alertdialog");
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      expect(deletes(fn)).toHaveLength(0);
    });

    it("confirming deletes the open chat, clears the screen and removes it from the sidebar", async () => {
      const fn = setup();
      const user = userEvent.setup();
      render(<App />);
      await user.click(await screen.findByRole("button", { name: /^Old chat/ }));
      expect(await screen.findByText("earlier answer")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Options for Old chat" }));
      await user.click(screen.getByRole("menuitem", { name: "Delete" }));
      await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(deletes(fn)).toHaveLength(1);
      expect(deletes(fn)[0][0]).toBe(`/api/sessions/${SID}`);
      expect(screen.queryByText("earlier answer")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Old chat/ })).not.toBeInTheDocument();
      expect(screen.getByText("No conversations yet.")).toBeInTheDocument();
    });

    it("keeps the chat and shows the error when deletion fails", async () => {
      setup({ status: 500, body: { error: { code: "internal_error", message: "An internal error occurred." } } });
      const user = userEvent.setup();
      render(<App />);
      await user.click(await screen.findByRole("button", { name: "Options for Old chat" }));
      await user.click(screen.getByRole("menuitem", { name: "Delete" }));
      const dlg = await screen.findByRole("alertdialog");
      await user.click(within(dlg).getByRole("button", { name: "Delete" }));
      expect(await within(dlg).findByRole("alert")).toHaveTextContent("An internal error occurred.");
      expect(screen.getByRole("button", { name: /^Old chat/ })).toBeInTheDocument();
    });
  });

  it("settings panel has no key-explainer text and no extra label under the Model heading", async () => {
    mockFetch(base);
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText(/is read from/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/never sent to this page/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Tool-capable model")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: "Model" })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Model")).toBeInTheDocument();
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
    expect(await within(dialog).findByText(/3 requests, 1,200 tokens/)).toBeInTheDocument();
    await user.selectOptions(within(dialog).getByLabelText("Model"), "m/b");
    await waitFor(() => expect(fn).toHaveBeenCalledWith("/api/settings", expect.objectContaining({ method: "PUT", body: JSON.stringify({ model: "m/b" }) })));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("warns when no key is configured", async () => {
    mockFetch((url, init) => (url === "/api/settings" ? { body: { ...SETTINGS, key_configured: false } } : base(url, init)));
    render(<App />);
    expect(await screen.findByText(/No API key is configured/)).toBeInTheDocument();
    expect(screen.getByText("NVIDIA_API_KEY")).toBeInTheDocument();
  });
});


