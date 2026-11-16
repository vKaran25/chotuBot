import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "../App";
import MessageBody from "./MessageBody";

describe("MessageBody", () => {
  it("renders bold, lists, headings and inline code instead of showing raw symbols", () => {
    const { container } = render(<MessageBody text={"## Plan\n\nThis is **important** and `code`.\n\n- one\n- two\n\n1. first\n2. second"} />);
    expect(screen.getByRole("heading", { name: "Plan" })).toBeInTheDocument();
    expect(screen.getByText("important").tagName).toBe("STRONG");
    expect(screen.getByText("code").tagName).toBe("CODE");
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(container.textContent).not.toContain("**");
    expect(container.textContent).not.toContain("##");
  });

  it("renders fenced code blocks and GFM tables", () => {
    const { container } = render(<MessageBody text={"```python\nprint('hi')\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |"} />);
    expect(container.querySelector("pre code")?.textContent).toContain("print('hi')");
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(container.querySelector(".md-table table")).not.toBeNull();
  });

  it("does not render raw HTML from the model", () => {
    const { container } = render(<MessageBody text={'hello <img src=x onerror="alert(1)"> <script>alert(2)</script> <b>raw</b>'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("neutralises javascript: links and opens real links safely", () => {
    const { container } = render(<MessageBody text={"[bad](javascript:alert(1)) and [good](https://example.com)"} />);
    const links = Array.from(container.querySelectorAll("a"));
    for (const a of links) expect(a.getAttribute("href") ?? "").not.toMatch(/^javascript:/i);
    const good = screen.getByRole("link", { name: "good" });
    expect(good).toHaveAttribute("href", "https://example.com");
    expect(good).toHaveAttribute("target", "_blank");
    expect(good.getAttribute("rel")).toContain("noopener");
  });
});

describe("App message rendering", () => {
  it("shows the user's own text literally and renders the assistant's Markdown", async () => {
    const SID = "a".repeat(32);
    const summary = { id: SID, title: "t", created_at: "2026-11-15T00:00:00+00:00", updated_at: "2026-11-15T00:00:00+00:00", message_count: 2 };
    const detail = { ...summary, messages: [
      { id: 1, role: "user", content: "what is **this**?", tool_activity: [] },
      { id: 2, role: "assistant", content: "It is **bold** text.", tool_activity: [] }] };
    const { vi } = await import("vitest");
    const userEvent = (await import("@testing-library/user-event")).default;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const body = url === "/api/sessions" ? { sessions: [summary] } : url === `/api/sessions/${SID}` ? detail
        : url === "/api/settings" ? { provider: "nim", key_configured: true, key_env_var: "NVIDIA_API_KEY", model: "m", models: [], tracing_enabled: false }
        : { totals: { requests: 0, total_tokens: 0, avg_latency_ms: 0 }, records: [] };
      return new Response(JSON.stringify(body), { status: 200 });
    }));
    render(<App />);
    await userEvent.setup().click(await screen.findByRole("button", { name: /^t/ }));
    expect(await screen.findByText("what is **this**?")).toBeInTheDocument(); // user text untouched
    expect(screen.getByText("bold").tagName).toBe("STRONG"); // assistant text rendered
    vi.unstubAllGlobals();
  });
});
