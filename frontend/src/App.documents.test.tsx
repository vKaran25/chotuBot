import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const SID = "a".repeat(32);
const SETTINGS = {
  provider: "nim", key_configured: true, key_env_var: "NVIDIA_API_KEY", model: "m/a",
  models: [{ id: "m/a", label: "A" }], tracing_enabled: false,
};
const WS = { id: "default", name: "Default", created_at: "x", document_count: 1, sharing: { allowed: false, updated_at: null } };
const CIT = { id: "c".repeat(32), n: 1, workspace_id: "default", document_id: "d".repeat(32), filename: "orion-handbook.pdf", location: { page: 1 }, status: "ok" };
const SESSION = {
  id: SID, title: "q", created_at: "x", updated_at: "x", message_count: 2,
  messages: [
    { id: 1, role: "user", content: "How long does the battery last?", tool_activity: [], request_id: "r1" },
    { id: 2, role: "assistant", content: "It lasts 14 hours [1].", tool_activity: [], request_id: "r1" },
  ],
};

type R = { status?: number; body: unknown } | undefined;
function mockFetch(handler: (url: string, init?: RequestInit) => R) {
  const calls: { url: string; body?: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: typeof init?.body === "string" ? JSON.parse(init.body) : init?.body });
    const r = handler(url, init) ?? base(url);
    if (!r) throw new Error(`unexpected fetch ${url}`);
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200 });
  }));
  return calls;
}
function base(url: string): R {
  if (url === "/api/sessions") return { body: { sessions: [] } };
  if (url === "/api/settings") return { body: SETTINGS };
  if (url === "/api/usage") return { body: { totals: { requests: 0, total_tokens: 0, avg_latency_ms: 0 }, records: [] } };
  if (url === "/api/workspaces") return { body: { workspaces: [WS], consent_required: true } };
  return undefined;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  try { localStorage.clear(); } catch { /* ignore */ }
});

describe("App with documents", () => {
  it("sends use_documents and the workspace only when the switch is on, and shows citations", async () => {
    const calls = mockFetch((url) => {
      if (url === "/api/chat") return { body: { session_id: SID, request_id: "r1", message: { role: "assistant", content: "x" }, tool_activity: [], usage: {}, citations: [CIT] } };
      if (url === `/api/sessions/${SID}`) return { body: SESSION };
      if (url === `/api/sessions/${SID}/citations`) return { body: { citations: { r1: [CIT] } } };
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Documents" }));
    const toggle = screen.getByRole("menuitemcheckbox", { name: /Use my documents/ });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(toggle);
    expect(screen.getByText("Documents: Default")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Message"), "How long does the battery last?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/It lasts 14 hours/)).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/chat")?.body).toEqual({
      message: "How long does the battery last?", session_id: null, use_documents: true, workspace_id: "default",
    });
    const sources = await screen.findByRole("list", { name: "Sources" });
    expect(within(sources).getByText(/orion-handbook\.pdf — page 1/)).toBeInTheDocument();
  });

  it("offers 'Allow sharing' when the server requires consent, then saves it", async () => {
    const calls = mockFetch((url) => {
      if (url === "/api/chat") return { status: 409, body: { error: { code: "document_consent_required", message: "Sharing document excerpts with the cloud model is off for this workspace." } } };
      if (url === "/api/workspaces/default/consent") return { body: { ...WS, sharing: { allowed: true, updated_at: "y" } } };
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Documents" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: /Use my documents/ }));
    await user.type(screen.getByLabelText("Message"), "q");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("document_consent_required");
    await user.click(screen.getByRole("button", { name: "Allow sharing for this workspace" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/workspaces/default/consent")).toBe(true));
    expect(calls.find((c) => c.url === "/api/workspaces/default/consent")?.body).toEqual({ allowed: true });
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByLabelText("Message")).toHaveValue("q"); // draft kept for resending
  });

  it("loads citations when reopening a conversation", async () => {
    mockFetch((url) => {
      if (url === "/api/sessions") return { body: { sessions: [{ id: SID, title: "q", created_at: "x", updated_at: "x", message_count: 2 }] } };
      if (url === `/api/sessions/${SID}`) return { body: SESSION };
      if (url === `/api/sessions/${SID}/citations`) return { body: { citations: { r1: [CIT] } } };
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^q/ }));
    expect(await screen.findByRole("list", { name: "Sources" })).toBeInTheDocument();
  });

  it("opens document management from the + menu", async () => {
    mockFetch((url) => (url.startsWith("/api/documents?") ? { body: { documents: [] } } : undefined));
    const user = userEvent.setup();
    render(<App />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Documents" }));
    await user.click(screen.getByRole("menuitem", { name: "Manage documents" }));
    expect(await screen.findByRole("dialog", { name: "Documents" })).toBeInTheDocument();
    expect(await screen.findByText("No documents in this workspace yet.")).toBeInTheDocument();
  });

  it("adds a file to the selected workspace from the + menu", async () => {
    const DOC = { id: "d".repeat(32), workspace_id: "default", filename: "notes.md", file_type: "md", size_bytes: 4,
      status: "indexed", chunk_count: 1, page_count: null, index_version: "v1", created_at: "x", updated_at: "x" };
    const calls = mockFetch((url, init) => (url === "/api/documents" && init?.method === "POST" ? { status: 201, body: DOC } : undefined));
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Documents" });
    await user.upload(screen.getByLabelText("Add a file"), new File(["# hi"], "notes.md", { type: "text/markdown" }));
    expect(await screen.findByText("Added notes.md to Default.")).toBeInTheDocument();
    const up = calls.find((c) => c.url === "/api/documents");
    expect((up?.body as FormData).get("workspace_id")).toBe("default");
  });

  it("closes the + menu with Escape", async () => {
    mockFetch(() => undefined);
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Documents" }));
    expect(screen.getByRole("menu", { name: "Documents" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
