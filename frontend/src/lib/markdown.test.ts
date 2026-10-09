import { describe, expect, it } from "vitest";

import { renderMarkdown } from "./markdown";

function dom(text: string): HTMLDivElement {
  const div = document.createElement("div");
  div.innerHTML = renderMarkdown(text);
  return div;
}

describe("renderMarkdown", () => {
  it("returns an empty string for nothing", () => {
    expect(renderMarkdown("")).toBe("");
    expect(renderMarkdown(null)).toBe("");
    expect(renderMarkdown(undefined)).toBe("");
  });

  it("renders headings, emphasis and lists", () => {
    const d = dom("## Purpose\n\nThe **core** ledger, *not* a copy.\n\n- one\n- two\n\n1. first\n2. second");
    expect(d.querySelector("h2")?.textContent).toBe("Purpose");
    expect(d.querySelector("strong")?.textContent).toBe("core");
    expect(d.querySelector("em")?.textContent).toBe("not");
    expect(Array.from(d.querySelectorAll("ul > li")).map((li) => li.textContent)).toEqual(["one", "two"]);
    expect(d.querySelectorAll("ol > li")).toHaveLength(2);
  });

  it("renders a table", () => {
    const d = dom("| Env | Host |\n| --- | --- |\n| prod | erp01 |");
    expect(d.querySelector("th")?.textContent).toBe("Env");
    expect(d.querySelectorAll("tbody td")).toHaveLength(2);
  });

  it("keeps a single line break", () => {
    const d = dom("Owner: Finance\nVendor: SAP");
    expect(d.querySelectorAll("p")).toHaveLength(1);
    expect(d.querySelectorAll("br")).toHaveLength(1);
  });

  it("opens a markdown link in a new tab", () => {
    const a = dom("See the [runbook](https://wiki.example.com/erp).").querySelector("a");
    expect(a?.textContent).toBe("runbook");
    expect(a?.getAttribute("href")).toBe("https://wiki.example.com/erp");
    expect(a?.getAttribute("target")).toBe("_blank");
    expect(a?.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("links a bare address and leaves the full stop after it as text", () => {
    const d = dom("Runbook: https://wiki.example.com/erp.");
    expect(d.querySelector("a")?.getAttribute("href")).toBe("https://wiki.example.com/erp");
    expect(d.textContent?.trim()).toBe("Runbook: https://wiki.example.com/erp.");
  });

  it("never links a javascript: address", () => {
    const d = dom("[click](javascript:alert(1))");
    expect(d.querySelector("a[href]")).toBeNull();
  });

  it("shows typed HTML as text", () => {
    const d = dom('<script>alert(1)</script> and <img src="x" onerror="alert(1)">');
    expect(d.querySelector("script")).toBeNull();
    expect(d.querySelector("img")).toBeNull();
    expect(d.textContent).toContain("<script>alert(1)</script>");
  });

  it("links to an image without loading it", () => {
    const d = dom("![logo](https://tracker.example.com/pixel.png)");
    expect(d.querySelector("img")).toBeNull();
    expect(d.querySelector("a")?.getAttribute("href")).toBe("https://tracker.example.com/pixel.png");
  });

  it("treats an indented line as text, not code", () => {
    const d = dom("Notes:\n\n    indented remark");
    expect(d.querySelector("pre")).toBeNull();
    expect(d.textContent).toContain("indented remark");
  });

  it("keeps a fenced code block", () => {
    const d = dom("```\nSELECT 1;\n```");
    expect(d.querySelector("pre > code")?.textContent).toBe("SELECT 1;\n");
  });
});
