import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import LifecycleBadge, { getCurrentPhase } from "./LifecycleBadge";

// ---------------------------------------------------------------------------
// getCurrentPhase — pure logic tests
// ---------------------------------------------------------------------------

describe("getCurrentPhase", () => {
  // Fix "today" to 2025-06-15 for deterministic tests
  const TODAY = "2025-06-15";

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(`${TODAY}T12:00:00Z`));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns null for undefined lifecycle", () => {
    expect(getCurrentPhase(undefined)).toBeNull();
  });

  it("returns null for empty lifecycle", () => {
    expect(getCurrentPhase({})).toBeNull();
  });

  it("returns 'active' when active date is in the past", () => {
    expect(getCurrentPhase({ active: "2024-01-01" })).toBe("active");
  });

  it("returns 'plan' when only plan date is set and in the past", () => {
    expect(getCurrentPhase({ plan: "2025-01-01" })).toBe("plan");
  });

  it("returns 'plan' when plan date is in the future", () => {
    expect(getCurrentPhase({ plan: "2026-01-01" })).toBe("plan");
  });

  it("returns 'endOfLife' when endOfLife date is in the past", () => {
    expect(
      getCurrentPhase({
        plan: "2020-01-01",
        active: "2021-01-01",
        endOfLife: "2025-01-01",
      })
    ).toBe("endOfLife");
  });

  it("returns 'phaseOut' when phaseOut is past but endOfLife is future", () => {
    expect(
      getCurrentPhase({
        active: "2023-01-01",
        phaseOut: "2025-06-01",
        endOfLife: "2026-01-01",
      })
    ).toBe("phaseOut");
  });

  it("returns 'phaseIn' when phaseIn is past but active is future", () => {
    expect(
      getCurrentPhase({
        plan: "2024-01-01",
        phaseIn: "2025-06-01",
        active: "2025-12-01",
      })
    ).toBe("phaseIn");
  });

  it("returns most advanced past phase (priority order)", () => {
    // All dates in the past — endOfLife has highest priority
    expect(
      getCurrentPhase({
        plan: "2020-01-01",
        phaseIn: "2021-01-01",
        active: "2022-01-01",
        phaseOut: "2023-01-01",
        endOfLife: "2024-01-01",
      })
    ).toBe("endOfLife");
  });
});

// ---------------------------------------------------------------------------
// LifecycleBadge component rendering
// ---------------------------------------------------------------------------

describe("LifecycleBadge", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-15T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders nothing when no lifecycle", () => {
    const { container } = render(<LifecycleBadge />);
    expect(container.firstChild).toBeNull();
  });

  it("renders Active chip", () => {
    render(<LifecycleBadge lifecycle={{ active: "2024-01-01" }} />);
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("renders End of Life chip", () => {
    render(<LifecycleBadge lifecycle={{ endOfLife: "2025-01-01" }} />);
    expect(screen.getByText("End of Life")).toBeInTheDocument();
  });

  it("renders Plan chip for future plan date", () => {
    render(<LifecycleBadge lifecycle={{ plan: "2026-01-01" }} />);
    expect(screen.getByText("Plan")).toBeInTheDocument();
  });

  it("renders Phase Out chip", () => {
    render(
      <LifecycleBadge
        lifecycle={{ active: "2023-01-01", phaseOut: "2025-06-01" }}
      />
    );
    expect(screen.getByText("Phase Out")).toBeInTheDocument();
  });
});

describe("getCurrentPhase asOfMs (time travel)", () => {
  const lc = {
    plan: "2020-01-01",
    active: "2022-01-01",
    phaseOut: "2030-01-01",
    endOfLife: "2032-01-01",
  };
  const at = (iso: string) => new Date(iso).getTime();

  it("reports the phase in force on the given date, not today", () => {
    expect(getCurrentPhase(lc, at("2021-01-01"))).toBe("plan");
    expect(getCurrentPhase(lc, at("2025-01-01"))).toBe("active");
    expect(getCurrentPhase(lc, at("2031-01-01"))).toBe("phaseOut");
    expect(getCurrentPhase(lc, at("2033-01-01"))).toBe("endOfLife");
  });

  it("reports nothing before the first phase date", () => {
    expect(getCurrentPhase(lc, at("2019-01-01"))).toBe("plan");
    expect(getCurrentPhase({ active: "2030-01-01" }, at("2019-01-01"))).toBeNull();
  });

  it("falls back to today when no date is given", () => {
    expect(getCurrentPhase(lc)).toBe(getCurrentPhase(lc, Date.now()));
  });
});

// ---------------------------------------------------------------------------
// Explicit stage and per-type vocabularies
// ---------------------------------------------------------------------------

describe("LifecycleBadge with stages", () => {
  const STAGES = [
    { key: "core", label: "Core", color: "#2e7d32", semantic: "operational" as const },
    {
      key: "sunset",
      label: "Sunset",
      color: "#ed6c02",
      semantic: "retiring" as const,
      translations: { en: "Sunsetting" },
    },
  ];

  it("shows an explicit stage that has no date", () => {
    render(<LifecycleBadge lifecycle={{}} stage="core" stages={STAGES} />);
    expect(screen.getByText("Core")).toBeInTheDocument();
  });

  it("lets the explicit stage win over the dates", () => {
    render(<LifecycleBadge lifecycle={{ sunset: "2000-01-01" }} stage="core" stages={STAGES} />);
    expect(screen.getByText("Core")).toBeInTheDocument();
    expect(screen.queryByText("Sunsetting")).not.toBeInTheDocument();
  });

  it("derives the stage from the dates, with the translated label", () => {
    render(<LifecycleBadge lifecycle={{ core: "2000-01-01", sunset: "2001-01-01" }} stages={STAGES} />);
    expect(screen.getByText("Sunsetting")).toBeInTheDocument();
  });

  it("renders nothing when the stage is unknown", () => {
    const { container } = render(<LifecycleBadge lifecycle={{}} stage={null} stages={STAGES} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("ignores built-in phase dates on a type with its own stages", () => {
    const { container } = render(
      <LifecycleBadge lifecycle={{ active: "2000-01-01" }} stages={STAGES} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a stage the vocabulary no longer defines by its key", () => {
    render(<LifecycleBadge stage="legacy" stages={STAGES} />);
    expect(screen.getByText("legacy")).toBeInTheDocument();
  });

  it("shows an explicit built-in phase with no dates", () => {
    render(<LifecycleBadge lifecycle={{}} stage="phaseOut" />);
    expect(screen.getByText("Phase Out")).toBeInTheDocument();
  });

  it("treats an empty vocabulary as the built-in phases", () => {
    render(<LifecycleBadge lifecycle={{ active: "2000-01-01" }} stages={[]} />);
    expect(screen.getByText("Active")).toBeInTheDocument();
  });
});
