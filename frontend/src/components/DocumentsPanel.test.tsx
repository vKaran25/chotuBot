import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DocumentsPanel from "./DocumentsPanel";
import type { Workspace } from "../types";

const WS: Workspace = {
  id: "default", name: "Default", created_at: "2026-11-18T00:00:00+00:00", document_count: 1,
  sharing: { allowed: false, updated_at: null },
};
const DOC = {
  id: "d".repeat(32), workspace_id: "default", filename: "notes.md", file_type: "md", size_bytes: 2048,
  status: "indexed", chunk_count: 3, page_count: null, index_version: "v1",
  created_at: "2026-11-18T00:00:00+00:00", updated_at: "2026-11-18T00:00:00+00:00",
};

type Call = { url: string; init?: RequestInit };
function mockApi(overrides: (c: Call) => { status?: number; body: unknown } | undefined = () => undefined) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const o = overrides({ url, init });
    if (o) return new Response(o.body === null ? null : JSON.stringify(o.body), { status: o.status ?? 200 });
    if (url.startsWith("/api/documents?")) return new Response(JSON.stringify({ documents: [DOC] }));
    throw new Error(`unexpected ${url}`);
  }));
  return calls;
}

const props = (extra = {}) => ({
  workspaces: [WS], consentRequired: true, workspaceId: "default",
  onSelectWorkspace: vi.fn(), onWorkspacesChanged: vi.fn(async () => {}), onClose: vi.fn(), ...extra,
});

beforeEach(() => vi.unstubAllGlobals());

describe("DocumentsPanel", () => {
  it("lists documents of the selected workspace", async () => {
    mockApi();
    render(<DocumentsPanel {...props()} />);
    const list = await screen.findByRole("list", { name: "Documents" });
    expect(within(list).getByText("notes.md")).toBeInTheDocument();
    expect(within(list).getByText(/MD · 2\.0 KB · 3 chunks/)).toBeInTheDocument();
  });

  it("uploads a file as multipart form data to the selected workspace", async () => {
    const calls = mockApi(({ url, init }) => (url === "/api/documents" && init?.method === "POST" ? { status: 201, body: DOC } : undefined));
    const p = props();
    const user = userEvent.setup();
    render(<DocumentsPanel {...p} />);
    const file = new File(["# hi"], "notes.md", { type: "text/markdown" });
    await user.upload(screen.getByLabelText("Upload a document (.pdf, .md, .txt, max 10 MB)"), file);
    const up = calls.find((c) => c.url === "/api/documents");
    expect(up?.init?.body).toBeInstanceOf(FormData);
    const form = up!.init!.body as FormData;
    expect(form.get("workspace_id")).toBe("default");
    expect((form.get("file") as File).name).toBe("notes.md");
    expect(up?.init?.headers).toBeUndefined(); // browser sets the multipart boundary
    expect(await screen.findByText("Uploaded notes.md.")).toBeInTheDocument();
    expect(p.onWorkspacesChanged).toHaveBeenCalled();
  });

  it("shows upload errors from the server", async () => {
    mockApi(({ url, init }) => url === "/api/documents" && init?.method === "POST"
      ? { status: 415, body: { error: { code: "unsupported_file_type", message: "Only .pdf, .md and .txt files are supported." } } }
      : undefined);
    const user = userEvent.setup({ applyAccept: false });
    render(<DocumentsPanel {...props()} />);
    await user.upload(screen.getByLabelText(/Upload a document/), new File(["x"], "a.docx"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only .pdf, .md and .txt files are supported.");
  });

  it("reindexes and deletes (after confirming) a document", async () => {
    const calls = mockApi(({ url, init }) => {
      if (url.includes("/reindex")) return { body: DOC };
      if (init?.method === "DELETE") return { status: 204, body: null };
      return undefined;
    });
    const user = userEvent.setup();
    render(<DocumentsPanel {...props()} />);
    await screen.findByText("notes.md");
    await user.click(screen.getByRole("button", { name: "Reindex notes.md" }));
    await waitFor(() => expect(calls.some((c) => c.url === `/api/documents/${DOC.id}/reindex?workspace_id=default`)).toBe(true));
    await user.click(screen.getByRole("button", { name: "Delete notes.md" }));
    expect(calls.some((c) => c.init?.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Confirm delete notes.md" }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "DELETE" && c.url === `/api/documents/${DOC.id}?workspace_id=default`)).toBe(true));
  });

  it("toggles the per-workspace sharing consent", async () => {
    const calls = mockApi(({ url }) => url === "/api/workspaces/default/consent"
      ? { body: { ...WS, sharing: { allowed: true, updated_at: "2026-11-18T01:00:00+00:00" } } } : undefined);
    const p = props();
    const user = userEvent.setup();
    render(<DocumentsPanel {...p} />);
    const box = screen.getByRole("checkbox", { name: "Allow sharing excerpts with the cloud model" });
    expect(box).not.toBeChecked();
    await user.click(box);
    const put = calls.find((c) => c.url === "/api/workspaces/default/consent");
    expect(JSON.parse(String(put?.init?.body))).toEqual({ allowed: true });
    expect(p.onWorkspacesChanged).toHaveBeenCalled();
  });

  it("explains that the demo provider needs no consent", () => {
    mockApi();
    render(<DocumentsPanel {...props({ consentRequired: false })} />);
    expect(screen.queryByRole("checkbox", { name: /Allow sharing/ })).not.toBeInTheDocument();
    expect(screen.getByText(/nothing leaves this computer/)).toBeInTheDocument();
  });

  it("creates a workspace and selects it", async () => {
    const NEW = { ...WS, id: "f".repeat(32), name: "Research", document_count: 0 };
    mockApi(({ url, init }) => url === "/api/workspaces" && init?.method === "POST" ? { status: 201, body: NEW } : undefined);
    const p = props();
    const user = userEvent.setup();
    render(<DocumentsPanel {...p} />);
    await user.click(screen.getByRole("button", { name: "New workspace" }));
    await user.type(screen.getByLabelText("Workspace name"), "Research");
    await user.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(p.onSelectWorkspace).toHaveBeenCalledWith(NEW.id));
  });
});
