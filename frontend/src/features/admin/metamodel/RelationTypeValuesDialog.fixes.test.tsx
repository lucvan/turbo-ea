/**
 * The relation "type values" editor, regression tests: a custom row's red
 * flag and the Save check read the same rule — the canonical `label`, the
 * fallback every locale without a translation shows — so a row never looks
 * valid while Save refuses it, or the reverse.
 *
 * Same harness as `RelationTypeValuesDialog.test.tsx`.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/client", () => import("@/test/apiMock").then((m) => m.apiClientModule()));
vi.mock("@/components/ColorPicker", () => ({
  default: ({ value, onChange }: { value: string; onChange: (c: string) => void }) => (
    <input aria-label="color" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

import { mockApi } from "@/test/apiMock";
import { makeField, makeOption, makeRelationType } from "@/test/fixtures/metamodel";
import type { FieldDef } from "@/types";
import RelationTypeValuesDialog from "./RelationTypeValuesDialog";

const DIM_NAME = "Type name (English)";

function relWith(field: FieldDef) {
  return makeRelationType({
    key: "relAppToITC",
    label: "runs on",
    source_type_key: "Application",
    target_type_key: "ITComponent",
    attributes_schema: [field],
  });
}
const PATH = "/metamodel/relation-types/relAppToITC";

function renderDialog(field: FieldDef) {
  const user = userEvent.setup();
  render(
    <RelationTypeValuesDialog open relationType={relWith(field)} onClose={vi.fn()} onSaved={vi.fn()} />,
  );
  return { user, dialog: screen.getByRole("dialog") };
}

const saveButton = () => screen.getByRole("button", { name: "Save" });

beforeEach(() => {
  mockApi.reset();
  mockApi.on("patch", PATH, {});
});

describe("RelationTypeValuesDialog — a stored row's label", () => {
  it("flags a row that carries only a translation, as Save refuses it, until it is given a label", async () => {
    const { user, dialog } = renderDialog(
      makeField({
        key: "tier",
        label: "",
        translations: { en: "Tier" },
        type: "single_select",
        options: [makeOption({ key: "gold", label: "", translations: { en: "Gold" } })],
      }),
    );
    const name = within(dialog).getByLabelText(DIM_NAME);
    const value = within(dialog).getByLabelText("Label");
    expect(name).toHaveValue("Tier");
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(value).toHaveAttribute("aria-invalid", "true");
    expect(saveButton()).toBeDisabled();

    // Typing a name gives the row its label as well as this locale's text.
    await user.clear(name);
    await user.type(name, "Tier");
    expect(name).toHaveAttribute("aria-invalid", "false");
    expect(saveButton()).toBeDisabled();
    await user.clear(value);
    await user.type(value, "Gold");
    expect(value).toHaveAttribute("aria-invalid", "false");
    expect(saveButton()).toBeEnabled();

    await user.click(saveButton());
    await waitFor(() => expect(mockApi.callsOf("patch", PATH)).toHaveLength(1));
    const [saved] = (mockApi.callsOf("patch", PATH)[0].body as { attributes_schema: FieldDef[] })
      .attributes_schema;
    expect(saved).toMatchObject({ label: "Tier", translations: { en: "Tier" } });
    expect(saved.options?.[0]).toMatchObject({ label: "Gold", translations: { en: "Gold" } });
  });

  it("does not flag a labelled row whose translation here is empty, and saves it", async () => {
    const { user, dialog } = renderDialog(
      makeField({
        key: "tier",
        label: "Tier",
        translations: { en: "" },
        type: "single_select",
        options: [makeOption({ key: "gold", label: "Gold", translations: { en: "  " } })],
      }),
    );
    expect(within(dialog).getByLabelText(DIM_NAME)).toHaveAttribute("aria-invalid", "false");
    expect(within(dialog).getByLabelText("Label")).toHaveAttribute("aria-invalid", "false");
    expect(saveButton()).toBeEnabled();

    await user.click(saveButton());
    await waitFor(() => expect(mockApi.callsOf("patch", PATH)).toHaveLength(1));
    const [saved] = (mockApi.callsOf("patch", PATH)[0].body as { attributes_schema: FieldDef[] })
      .attributes_schema;
    // The empty translation is dropped, so the label is what every locale shows.
    expect(saved.label).toBe("Tier");
    expect(saved.translations).toBeUndefined();
    expect(saved.options?.[0].label).toBe("Gold");
    expect(saved.options?.[0].translations).toBeUndefined();
  });
});
