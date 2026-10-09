/**
 * Tests for the shared "Show on card" dropdown — what each card says, as
 * distinct from what colours it. Carried over from the combined menu the
 * diagram's two buttons replaced, and since extended to the Layered Dependency
 * View, which is why the trigger, the extra lines and the phone shell are
 * covered here too.
 */
import { render, screen, waitForElementToBeRemoved, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setViewportWidth } from "@/test/matchMedia";
import ShowOnCardSelector from "./ShowOnCardSelector";
import type { CardLabelSettings } from "@/lib/cardDisplayFields";
import type { CardType } from "@/types";

function type(key: string, fields: Array<{ key: string; label: string }>): CardType {
  return {
    key,
    label: key,
    icon: "apps",
    color: "#0f7eb5",
    category: "Application & Data",
    fields_schema: [{ section: "Details", fields: fields.map((f) => ({ type: "text", ...f })) }],
  } as unknown as CardType;
}

const TYPES = [
  type("Application", [
    { key: "owner", label: "Owner" },
    { key: "criticality", label: "Criticality" },
  ]),
  type("ITComponent", [
    { key: "owner", label: "Owner" },
    { key: "hosting", label: "Hosting" },
  ]),
  type("Provider", [{ key: "tier", label: "Tier" }]),
];

function setup(labels: CardLabelSettings = { fields: [] }, maxLines?: number) {
  const onChange = vi.fn();
  const onOpen = vi.fn();
  render(
    <ShowOnCardSelector
      activeTypeKeys={["Application", "ITComponent"]}
      onOpen={onOpen}
      types={TYPES}
      labels={labels}
      onChange={onChange}
      maxLines={maxLines}
    />,
  );
  return { onChange, onOpen };
}

async function openMenu() {
  await userEvent.click(screen.getByRole("button"));
  return screen.getByRole("menu");
}

// setup.ts resets to 1280 before each test; undo any per-test narrowing so a
// mobile case cannot leak into the next file.
afterEach(() => setViewportWidth(1280));

describe("ShowOnCardSelector", () => {
  it("files a field shared by two canvas types under Shared, exactly once", async () => {
    setup();
    const menu = await openMenu();
    expect(within(menu).getByText("Shared")).toBeInTheDocument();
    expect(within(menu).getAllByText("Owner")).toHaveLength(1);
  });

  it("heads each remaining field with the card type that owns it", async () => {
    setup();
    const menu = await openMenu();
    expect(within(menu).getByText("Hosting")).toBeInTheDocument();
    expect(within(menu).getAllByText("Application").length).toBeGreaterThan(0);
  });

  it("offers nothing from a card type that is not on the canvas", async () => {
    setup();
    const menu = await openMenu();
    expect(within(menu).queryByText("Tier")).not.toBeInTheDocument();
  });

  it("counts what is currently drawn on the shape", async () => {
    setup({ fields: ["owner"], showType: true });
    expect(screen.getByRole("button")).toHaveTextContent("(2)");
  });

  it("adds and removes a field", async () => {
    const { onChange } = setup({ fields: ["owner"] });
    const menu = await openMenu();
    await userEvent.click(within(menu).getByText("Hosting"));
    expect(onChange).toHaveBeenCalledWith({ fields: ["owner", "hosting"] });
    await userEvent.click(within(menu).getByText("Owner"));
    expect(onChange).toHaveBeenCalledWith({ fields: [] });
  });

  it("toggles the card type and subtype lines", async () => {
    const { onChange } = setup();
    const menu = await openMenu();
    await userEvent.click(within(menu).getByText("Card type"));
    expect(onChange).toHaveBeenCalledWith({ fields: [], showType: true });
  });

  it("offers the alias as a fixed row and toggles it (#1211)", async () => {
    // The alias is a card column, not an attribute, so the field catalogue
    // never lists it — it needs a row of its own like type and subtype.
    const { onChange } = setup();
    const menu = await openMenu();
    await userEvent.click(within(menu).getByText("Alias"));
    expect(onChange).toHaveBeenCalledWith({ fields: [], showAlias: true });
  });

  it("un-ticks an alias row that is on", async () => {
    const { onChange } = setup({ fields: [], showAlias: true });
    const menu = await openMenu();
    await userEvent.click(within(menu).getByText("Alias"));
    expect(onChange).toHaveBeenCalledWith({ fields: [], showAlias: false });
  });

  it("lists the alias row after Subtype and before the field groups", async () => {
    setup();
    const menu = await openMenu();
    const names = within(menu)
      .getAllByRole("menuitem")
      .map((el) => el.textContent?.trim());
    const subtypeAt = names.indexOf("Subtype");
    expect(subtypeAt).toBeGreaterThan(-1);
    expect(names[subtypeAt + 1]).toBe("Alias");
  });

  it("ticks the Alias row to match the setting", async () => {
    setup({ fields: [], showAlias: true });
    const menu = await openMenu();
    const row = within(menu).getByText("Alias").closest("li") as HTMLElement;
    expect(within(row).getByRole("checkbox")).toBeChecked();
  });

  it("leaves the Alias row unticked when the alias is off", async () => {
    setup({ fields: [], showAlias: false });
    const menu = await openMenu();
    const row = within(menu).getByText("Alias").closest("li") as HTMLElement;
    expect(within(row).getByRole("checkbox")).not.toBeChecked();
  });

  it("counts the alias row on the button", () => {
    setup({ fields: [], showAlias: true });
    expect(screen.getByRole("button")).toHaveTextContent("(1)");
  });

  it("re-scans the canvas every time the menu opens, and stays open", async () => {
    const { onOpen } = setup();
    const menu = await openMenu();
    expect(onOpen).toHaveBeenCalledTimes(1);
    await userEvent.click(within(menu).getByText("Subtype"));
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("labels the icon trigger and badges it with the count", async () => {
    const onChange = vi.fn();
    render(
      <ShowOnCardSelector
        trigger="icon"
        activeTypeKeys={["Application"]}
        types={TYPES}
        labels={{ fields: ["owner"], showType: true }}
        onChange={onChange}
      />,
    );
    // Icon-only, so the accessible name has to come from aria-label, and the
    // count has to be visible without reading the label.
    const button = screen.getByRole("button", { name: "Show on card" });
    expect(button).toHaveTextContent("2");
  });

  it("renders caller-supplied extra lines and counts the ticked ones", async () => {
    const onSet = vi.fn();
    const onChange = vi.fn();
    render(
      <ShowOnCardSelector
        activeTypeKeys={["Application"]}
        types={TYPES}
        labels={{ fields: [] }}
        onChange={onChange}
        extraLines={[
          { key: "showLifecycle", label: "Lifecycle", checked: true, onSet },
        ]}
      />,
    );
    expect(screen.getByRole("button")).toHaveTextContent("(1)");
    const menu = await openMenu();
    await userEvent.click(within(menu).getByText("Lifecycle"));
    // Ticked, so clicking the row asks for it to be turned off.
    expect(onSet).toHaveBeenCalledWith(false);
    // The caller owns the state, so the component must not also fire onChange.
    expect(onChange).not.toHaveBeenCalled();
  });

  it("opens the same rows in a full-screen sheet on a phone", async () => {
    setViewportWidth(500);
    setup();
    await userEvent.click(screen.getByRole("button"));

    const dialog = screen.getByRole("dialog");
    // Same tree as the dropdown: a MenuList, not a bare Box, so assistive tech
    // and these tests see one shape on both viewports.
    expect(within(dialog).getByRole("menu")).toBeInTheDocument();
    expect(within(dialog).getByText("Hosting")).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    // The dialog outlives the click by its exit transition.
    await waitForElementToBeRemoved(() => screen.queryByRole("dialog"));
  });

  it("says so when no card type in play has any fields", async () => {
    const onChange = vi.fn();
    render(
      <ShowOnCardSelector
        activeTypeKeys={[]}
        types={TYPES}
        labels={{ fields: [] }}
        onChange={onChange}
      />,
    );
    const menu = await openMenu();
    expect(within(menu).getByText("No fields available")).toBeInTheDocument();
  });

  it("clears every tick the shared settings own in one update", async () => {
    const { onChange } = setup({
      fields: ["owner", "hosting"],
      showType: true,
      showSubtype: true,
      showAlias: true,
    });
    const menu = await openMenu();
    await userEvent.click(within(menu).getByRole("button", { name: "Clear all" }));
    // One call, not one per tick — the store takes a single write and the
    // reader gets a single undo step.
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({
      fields: [],
      showType: false,
      showSubtype: false,
      showAlias: false,
    });
  });

  it("leaves an already-unticked extra line alone when clearing", async () => {
    const onSet = vi.fn();
    const onChange = vi.fn();
    render(
      <ShowOnCardSelector
        activeTypeKeys={["Application"]}
        types={TYPES}
        labels={{ fields: ["owner"] }}
        onChange={onChange}
        extraLines={[{ key: "showLifecycle", label: "Lifecycle", checked: false, onSet }]}
      />,
    );
    const menu = await openMenu();
    await userEvent.click(within(menu).getByRole("button", { name: "Clear all" }));
    // The reason the line contract sets rather than flips: a flip here would
    // switch lifecycle ON while clearing everything else.
    expect(onSet).toHaveBeenCalledWith(false);
    expect(onSet).not.toHaveBeenCalledWith(true);
  });

  it("offers nothing to clear when the card is already bare", async () => {
    setup({ fields: [] });
    const menu = await openMenu();
    expect(within(menu).getByRole("button", { name: "Clear all" })).toBeDisabled();
  });

  it("names the ceiling only on a surface that has one", async () => {
    // The Layered Dependency View lays its nodes out at a fixed size.
    setup({ fields: [] }, 2);
    const menu = await openMenu();
    expect(menu).toHaveTextContent("The first 2 appear on the card.");
  });

  it("promises every pick where the shape can grow", async () => {
    // The diagram editor passes no ceiling: a DrawIO cell is resized to fit.
    setup({ fields: [] });
    const menu = await openMenu();
    expect(menu).toHaveTextContent("Every pick appears on the card, which grows to fit.");
  });
});
