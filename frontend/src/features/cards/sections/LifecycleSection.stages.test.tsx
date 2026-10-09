/**
 * The lifecycle section on a card whose type defines its own stages, and the
 * explicit current stage on any card: it needs no date, it wins over the
 * dates, and a card with neither reads as unknown.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => import("@/test/apiMock").then((m) => m.apiClientModule()));
vi.mock("@/hooks/useMetamodel", () => import("@/test/hooks").then((m) => m.useMetamodelModule()));
vi.mock("@/hooks/useDateFormat", () => ({
  useDateFormat: () => ({ formatDate: (v: string) => `d:${v}` }),
}));

import { RADAR_PRESET } from "@/lib/lifecycleStages";
import { makeCardType } from "@/test/fixtures/metamodel";
import { withMetamodel } from "@/test/hooks";
import type { Card } from "@/types";
import LifecycleSection from "./LifecycleSection";

const card = (over: Partial<Card> = {}) =>
  ({ id: "c1", type: "Application", name: "App", lifecycle: {}, ...over }) as unknown as Card;

beforeEach(() =>
  withMetamodel([
    makeCardType({ key: "Application", lifecycle_config: { stages: RADAR_PRESET } }),
    makeCardType({ key: "Interface" }),
  ]),
);

function startEditing() {
  fireEvent.click(screen.getAllByRole("button").slice(-1)[0]);
}

async function pickStage(name: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "Current stage" }));
  fireEvent.click(within(await screen.findByRole("listbox")).getByText(name));
}

describe("LifecycleSection — configurable stages", () => {
  it("lays out the type's own stages in order", () => {
    render(<LifecycleSection card={card()} onSave={async () => {}} />);
    for (const label of ["Evaluating", "Emerging", "Core", "Heritage", "Sunset", "Discontinued"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByText("Phase In")).toBeNull();
  });

  it("keeps the built-in phases on a type without its own stages", () => {
    render(<LifecycleSection card={card({ type: "Interface" })} onSave={async () => {}} />);
    expect(screen.getByText("Phase In")).toBeInTheDocument();
    expect(screen.queryByText("Heritage")).toBeNull();
  });

  it("reads as unknown when nothing is recorded", () => {
    render(<LifecycleSection card={card()} onSave={async () => {}} />);
    expect(screen.getByText("Unknown")).toBeInTheDocument();
  });

  it("shows a stated stage that has no date", () => {
    render(<LifecycleSection card={card({ lifecycle_stage: "heritage" })} onSave={async () => {}} />);
    expect(screen.getAllByText("Heritage")).toHaveLength(2);
    expect(screen.getByText("stated")).toBeInTheDocument();
    expect(screen.queryByText("Unknown")).toBeNull();
  });

  it("says when the stage comes from the dates", () => {
    render(
      <LifecycleSection card={card({ lifecycle: { core: "2000-01-01" } })} onSave={async () => {}} />,
    );
    expect(screen.getByText("from the dates")).toBeInTheDocument();
  });

  it("flags dates that disagree with the stated stage", () => {
    render(
      <LifecycleSection
        card={card({ lifecycle: { sunset: "2000-01-01" }, lifecycle_stage: "core" })}
        onSave={async () => {}}
      />,
    );
    expect(screen.getByLabelText("The dates put this card in Sunset")).toBeInTheDocument();
  });

  it("saves a stage without touching the dates", async () => {
    const onSave = vi.fn(async () => {});
    render(<LifecycleSection card={card({ lifecycle: { core: "2020-01-01" } })} onSave={onSave} />);
    startEditing();
    await pickStage("Sunset");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({
        lifecycle: { core: "2020-01-01" },
        lifecycle_stage: "sunset",
      }),
    );
  });

  it("clears a stated stage back to null", async () => {
    const onSave = vi.fn(async () => {});
    render(<LifecycleSection card={card({ lifecycle_stage: "core" })} onSave={onSave} />);
    startEditing();
    await pickStage("Not stated (use the dates)");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({ lifecycle: {}, lifecycle_stage: null }),
    );
  });

  it("leaves the stage out of a save that did not change it", async () => {
    const onSave = vi.fn(async () => {});
    render(<LifecycleSection card={card({ lifecycle_stage: "core" })} onSave={onSave} />);
    startEditing();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ lifecycle: {} }));
  });

  it("keeps a stored stage the vocabulary no longer defines selectable", () => {
    render(<LifecycleSection card={card({ lifecycle_stage: "legacy" })} onSave={async () => {}} />);
    startEditing();
    expect(screen.getByRole("combobox", { name: "Current stage" })).toHaveTextContent("legacy");
  });

  it("restores the stored stage on cancel", async () => {
    render(<LifecycleSection card={card({ lifecycle_stage: "core" })} onSave={async () => {}} />);
    startEditing();
    await pickStage("Sunset");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("stated")).toBeInTheDocument();
    expect(screen.getAllByText("Core")).toHaveLength(2);
  });
});
