import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const SID = "a".repeat(32);
const WSID = "b".repeat(32);
const SETTINGS = {
  provider: "nim", key_configured: true, key_env_var: "NVIDIA_API_KEY", model: "m/a",
  models: [{ id: "m/a", label: "A" }], tracing_enabled: false,
};
const DEFAULT_WS = { id: "default", name: "Default", created_at: "x", document_count: 0, sharing: { allowed: false, updated_at: null } };
const CHAT_WS = { id: WSID, name: `chat:${SID}`, created_at: "x", document_count: 1, sharing: { allowed: true, updated_at: "y" } };
const DOC = { id: "d".repeat(32), workspace_id: WSID, filename: "orion-handbook.pdf", file_type: "pdf", size_bytes: 4,
  status: "indexed", chunk_count: 1, page_count: 1, index_version: "v1", created_at: "x", updated_at: "x" };
const CIT = { id: "c".repeat(32), n: 1, workspace_id: WSID, document_id: DOC.id, filename: "orion-handbook.pdf", location: { page: 1 }, status: "ok" };
const SUMMARY = { id: SID, title: "q", created_at: "x", updated_at: "x", message_count: 2 };
const SESSION = {
  ...SUMMARY,
  messages: [
    { id: 1, role: "user", content: "How long does the battery last?", tool_activity: [], request_id: "r1" },
    { id: 2, role: "assistant", content: "It lasts 14 hours [1].", tool_activity: [], request_id: "r1" },
  ],
};
const CHAT_RES = { session_id: SID, request_id: "r1", message: { role: "assistant", content: "It lasts 14 hours [1]." },
  tool_activity: [], usage: {}, citations: [CIT] };

type Call = { url: string; method: string; body?: unknown };
type R = { status?: number; body: unknown } | Response | undefined;
function mockFetch(handler: (url: string, init?: RequestInit) => R, workspaces: unknown[] = [DEFAULT_WS]) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: typeof init?.body === "string" ? JSON.parse(init.body) : init?.body });
    const r = handler(url, init) ?? base(url, workspaces);
    if (!r) throw new Error(`unexpected fetch ${url}`);
    if (r instanceof Response) return r;
    return new Response(r.body === null ? null : JSON.stringify(r.body), { status: r.status ?? 200 });
  }));
  return calls;
}
function base(url: string, workspaces: unknown[]): R {
  if (url === "/api/sessions") return { body: { sessions: [] } };
  if (url === "/api/settings") return { body: SETTINGS };
  if (url === "/api/usage") return { body: { totals: { requests: 0, total_tokens: 0, avg_latency_ms: 0 }, records: [] } };
  if (url === "/api/workspaces") return { body: { workspaces, consent_required: true } };
  return undefined;
}
const sse = (...events: unknown[]) =>
  new Response(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } });

beforeEach(() => vi.unstubAllGlobals());

describe("App with files", () => {
  it("+ opens the file picker directly: no menu, no documents button", async () => {
    mockFetch(() => undefined);
    render(<App />);
    expect(await screen.findByRole("button", { name: "Add files" })).toBeInTheDocument();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Documents" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Allow sharing/)).not.toBeInTheDocument();
  });

  it("adding a file in a new chat creates the chat and its file space, then questions use the file", async () => {
    const calls = mockFetch((url, init) => {
      if (url === "/api/sessions" && init?.method === "POST") return { status: 201, body: { ...SUMMARY, message_count: 0 } };
      if (url === "/api/workspaces" && init?.method === "POST") return { status: 201, body: { ...CHAT_WS, sharing: { allowed: false, updated_at: null } } };
      if (url === `/api/workspaces/${WSID}/consent`) return { body: CHAT_WS };
      if (url === "/api/documents" && init?.method === "POST") return { status: 201, body: DOC };
      if (url === "/api/chat") return { body: CHAT_RES };
      if (url === `/api/sessions/${SID}`) return { body: SESSION };
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Add files" });
    await user.upload(screen.getByLabelText("Choose files"), new File(["%PDF-"], "orion-handbook.pdf", { type: "application/pdf" }));
    const chips = await screen.findByRole("list", { name: "Attached files" });
    await waitFor(() => expect(within(chips).getByRole("button", { name: "Remove orion-handbook.pdf" })).toBeInTheDocument());
    expect(calls.find((c) => c.url === "/api/workspaces" && c.method === "POST")?.body).toEqual({ name: `chat:${SID}` });
    expect(calls.find((c) => c.url === `/api/workspaces/${WSID}/consent`)?.body).toEqual({ allowed: true });
    expect((calls.find((c) => c.url === "/api/documents")?.body as FormData).get("workspace_id")).toBe(WSID);

    await user.type(screen.getByLabelText("Message"), "How long does the battery last?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/It lasts 14 hours/)).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/chat")?.body).toEqual({
      message: "How long does the battery last?", session_id: SID, use_documents: true, workspace_id: WSID,
    });
    expect(await screen.findByRole("list", { name: "Sources" })).toHaveTextContent("orion-handbook.pdf — page 1");
  });

  it("reopening a chat shows its files and sources; removing a file deletes it", async () => {
    const calls = mockFetch((url, init) => {
      if (url === "/api/sessions") return { body: { sessions: [SUMMARY] } };
      if (url === `/api/sessions/${SID}`) return { body: SESSION };
      if (url === `/api/sessions/${SID}/citations`) return { body: { citations: { r1: [CIT] } } };
      if (url === `/api/documents?workspace_id=${WSID}`) return { body: { documents: [DOC] } };
      if (init?.method === "DELETE") return { status: 204, body: null };
      return undefined;
    }, [DEFAULT_WS, CHAT_WS]);
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^q/ }));
    expect(await screen.findByRole("list", { name: "Sources" })).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Remove orion-handbook.pdf" }));
    await waitFor(() => expect(screen.queryByRole("list", { name: "Attached files" })).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "DELETE" && c.url === `/api/documents/${DOC.id}?workspace_id=${WSID}`)).toBe(true);
  });

  it("a chat without files sends the plain Phase 1 body", async () => {
    const calls = mockFetch((url) => {
      if (url === "/api/chat") return { body: { ...CHAT_RES, citations: undefined, message: { role: "assistant", content: "hi" } } };
      if (url === `/api/sessions/${SID}`) return { body: { ...SESSION, messages: [] } };
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Message"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/api/chat")?.body).toEqual({ message: "hello", session_id: null }));
  });

  it("shows upload errors", async () => {
    mockFetch((url, init) => {
      if (url === "/api/sessions" && init?.method === "POST") return { status: 201, body: SUMMARY };
      if (url === "/api/workspaces" && init?.method === "POST") return { status: 201, body: CHAT_WS };
      if (url === `/api/workspaces/${WSID}/consent`) return { body: CHAT_WS };
      if (url === "/api/documents") return { status: 422, body: { error: { code: "no_extractable_text", message: "The PDF has no text layer (scanned PDFs are not supported)." } } };
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Add files" });
    await user.upload(screen.getByLabelText("Choose files"), new File(["%PDF-"], "scan.pdf"));
    expect(await screen.findByRole("alert")).toHaveTextContent("scan.pdf: The PDF has no text layer");
    expect(screen.queryByRole("list", { name: "Attached files" })).not.toBeInTheDocument();
  });

  it("deleting a chat deletes its files", async () => {
    const calls = mockFetch((url, init) => {
      if (url === "/api/sessions" && (init?.method ?? "GET") === "GET") return { body: { sessions: [SUMMARY] } };
      if (url === `/api/documents?workspace_id=${WSID}`) return { body: { documents: [DOC] } };
      if (init?.method === "DELETE") return { status: 204, body: null };
      return undefined;
    }, [DEFAULT_WS, CHAT_WS]);
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Options for q" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.startsWith(`/api/documents/${DOC.id}`))).toBe(true));
    expect(calls.some((c) => c.method === "DELETE" && c.url === `/api/sessions/${SID}`)).toBe(true);
  });

  it("streams the reply while it is being written", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const enc = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      async start(c) {
        c.enqueue(enc.encode(`data: ${JSON.stringify({ type: "delta", text: "Hello " })}\n\n`));
        await gate;
        c.enqueue(enc.encode(`data: ${JSON.stringify({ type: "delta", text: "world" })}\n\n`));
        c.enqueue(enc.encode(`data: ${JSON.stringify({ type: "done", response: { ...CHAT_RES, citations: undefined, message: { role: "assistant", content: "Hello world" } } })}\n\n`));
        c.close();
      },
    });
    const calls = mockFetch((url) => {
      if (url === "/api/chat") return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
      if (url === `/api/sessions/${SID}`) return { body: { ...SESSION, messages: [
        { id: 1, role: "user", content: "hi", tool_activity: [] },
        { id: 2, role: "assistant", content: "Hello world", tool_activity: [], request_id: "r1" }] } };
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Hello")).toBeInTheDocument(); // partial answer visible
    expect(screen.getByRole("button", { name: "Sending…" })).toBeDisabled();
    release();
    expect(await screen.findByText("Hello world")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument());
    const sent = (vi.mocked(fetch).mock.calls.find((c) => c[0] === "/api/chat")?.[1] as RequestInit).headers as Record<string, string>;
    expect(sent.Accept).toContain("text/event-stream");
    expect(calls.filter((c) => c.url === "/api/chat")).toHaveLength(1);
  });

  it("a stream that ends with an error shows it and keeps the draft", async () => {
    mockFetch((url) =>
      url === "/api/chat" ? sse({ type: "delta", text: "partial" }, { type: "error", error: { code: "provider_unavailable", message: "The model provider is unavailable or returned an error." } }) : undefined,
    );
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("provider_unavailable");
    expect(screen.getByLabelText("Message")).toHaveValue("hi");
    expect(screen.queryByText("partial")).not.toBeInTheDocument();
  });
});
