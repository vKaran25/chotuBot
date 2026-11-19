import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CitationList, { formatLocation } from "./CitationList";
import type { Citation } from "../types";

const OK: Citation = {
  id: "c".repeat(32), n: 1, workspace_id: "default", document_id: "d".repeat(32),
  filename: "orion-handbook.pdf", location: { page: 1 }, status: "ok",
};
const GONE: Citation = { ...OK, id: "e".repeat(32), n: 2, document_id: null, filename: null, location: null, status: "source_deleted" };

beforeEach(() => vi.unstubAllGlobals());

describe("formatLocation", () => {
  it("formats PDF pages, Markdown headings and line ranges", () => {
    expect(formatLocation({ page: 3 })).toBe("page 3");
    expect(formatLocation({ heading: "Notes > Ladakh", line_start: 5, line_end: 9 })).toBe("Notes > Ladakh · lines 5–9");
    expect(formatLocation({ heading: null, line_start: 1, line_end: 1 })).toBe("line 1");
    expect(formatLocation(null)).toBe("");
  });
});

describe("CitationList", () => {
  it("lists numbered sources and opens an excerpt with file name and location", async () => {
    const fetchFn = vi.fn(async () =>
      new Response(JSON.stringify({ ...OK, request_id: "r", session_id: "s", text: "Its main battery lasts 14 hours." })),
    );
    vi.stubGlobal("fetch", fetchFn);
    const user = userEvent.setup();
    render(<CitationList citations={[OK, GONE]} />);
    const list = screen.getByRole("list", { name: "Sources" });
    expect(list).toHaveTextContent("[1] orion-handbook.pdf — page 1");
    expect(list).toHaveTextContent("[2] source deleted");
    await user.click(screen.getByRole("button", { name: /\[1\] orion-handbook\.pdf/ }));
    const dialog = await screen.findByRole("dialog", { name: "Source [1]" });
    expect(dialog).toHaveTextContent("orion-handbook.pdf");
    expect(dialog).toHaveTextContent("page 1");
    expect(await screen.findByText("Its main battery lasts 14 hours.")).toBeInTheDocument();
    expect(fetchFn).toHaveBeenCalledWith(`/api/citations/${OK.id}`, expect.anything());
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows 'source deleted' for a citation whose document is gone", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ ...GONE, request_id: "r", session_id: "s", text: null })),
    ));
    const user = userEvent.setup();
    render(<CitationList citations={[GONE]} />);
    await user.click(screen.getByRole("button", { name: /source deleted/ }));
    expect(await screen.findByText("This source was deleted.")).toBeInTheDocument();
  });

  it("renders nothing without citations", () => {
    const { container } = render(<CitationList citations={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
