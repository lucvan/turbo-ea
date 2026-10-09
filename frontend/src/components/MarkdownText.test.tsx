import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import MarkdownText from "./MarkdownText";

describe("MarkdownText", () => {
  it("renders nothing for an empty value", () => {
    const { container } = render(<MarkdownText text={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders markdown as formatted blocks", () => {
    render(<MarkdownText text={"### Scope\n\n- **Ledger**\n- Reporting"} />);
    expect(screen.getByRole("heading", { level: 3, name: "Scope" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("Ledger").tagName).toBe("STRONG");
  });

  it("does not inherit pre-wrap from its parent", () => {
    const { container } = render(
      <div style={{ whiteSpace: "pre-wrap" }}>
        <MarkdownText text={"one\n\ntwo"} />
      </div>,
    );
    const block = container.querySelector(".markdown-text") as HTMLElement;
    expect(getComputedStyle(block).whiteSpace).toBe("normal");
  });
});
