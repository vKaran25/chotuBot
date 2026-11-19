import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import MessageBody from "./MessageBody";
import type { Citation } from "../types";

const PDF: Citation = {
  id: "citation-1", n: 1, workspace_id: "default", document_id: "handbook",
  filename: "orion-handbook.pdf", location: { page: 1 }, status: "ok",
};

describe("Reply sources", () => {
  it("shows each filename once even when multiple passages from it were cited", () => {
    render(<MessageBody text="The battery lasts 14 hours [1] [2]." citations={[
      PDF,
      { ...PDF, id: "citation-2", n: 2, location: { page: 5 } },
      { ...PDF, id: "citation-3", n: 3, filename: "field-notes.md" },
    ]} />);

    expect(screen.getByText("The battery lasts 14 hours.")).toBeInTheDocument();
    const sources = screen.getByRole("list", { name: "Sources" });
    expect(within(sources).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "orion-handbook.pdf", "field-notes.md",
    ]);
    expect(within(sources).queryByRole("button")).not.toBeInTheDocument();
  });

  it("preserves code, link labels, and numbers that do not identify a citation", () => {
    render(<MessageBody text="Value `[1]`, link [[1]](https://example.com), and [9]. See the manual [1]." citations={[PDF]} />);

    expect(screen.getByText("[1]", { selector: "code" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "[1]" })).toBeInTheDocument();
    expect(screen.getByText(/\[9\]\. See the manual\./)).toBeInTheDocument();
  });

  it("omits the source list for ordinary replies and keeps their bracketed numbers", () => {
    render(<MessageBody text="See item [1]." />);

    expect(screen.getByText("See item [1].")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Sources" })).not.toBeInTheDocument();
  });

  it("labels a deleted source in the same small source list", () => {
    render(<MessageBody text="The battery lasts 14 hours [1]." citations={[
      { ...PDF, status: "source_deleted", document_id: null, filename: null, location: null },
    ]} />);

    expect(screen.getByRole("list", { name: "Sources" })).toHaveTextContent("Source deleted");
  });
});
