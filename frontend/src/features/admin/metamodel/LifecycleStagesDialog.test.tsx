/**
 * The lifecycle stage editor: stored keys stay locked, a preset fills the
 * list, order follows the rows, and a removal the server refuses because cards
 * use the stage turns into an explicit reassignment the admin has to choose.
 * `ColorPicker` is stubbed with a plain input.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/client", () => import("@/test/apiMock").then((m) => m.apiClientModule()));
vi.mock("@/components/ColorPicker", () => ({
  default: ({ value, onChange }: { value: string; onChange: (c: string) => void }) => (
    <input aria-label="color" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

import { mockApi } from "@/test/apiMock";
import { makeCardType } from "@/test/fixtures/metamodel";
import { RADAR_PRESET } from "@/lib/lifecycleStages";
import LifecycleStagesDialog from "./LifecycleStagesDialog";

const APP = makeCardType({
  key: "Application",
  lifecycle_config: { stages: RADAR_PRESET.slice(2, 5) }, // core, heritage, sunset
});
const BARE = makeCardType({ key: "Interface" });
const PATH = "/metamodel/types/Application";

function renderDialog(cardType = APP) {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  const user = userEvent.setup();
  render(<LifecycleStagesDialog open cardType={cardType} onClose={onClose} onSaved={onSaved} />);
  return { user, onClose, onSaved, dialog: screen.getByRole("dialog") };
}

const keys = () =>
  screen.getAllByLabelText(/^Key/).map((k) => (k as HTMLInputElement).value);

type Body = {
  lifecycle_config: { stages: Record<string, unknown>[] };
  lifecycle_reassign?: Record<string, string>;
};
const patchBodies = (path = PATH) => mockApi.callsOf("patch", path).map((c) => c.body as Body);

beforeEach(() => {
  mockApi.reset();
  mockApi.on("patch", /^\/metamodel\/types\//, {});
});

describe("LifecycleStagesDialog", () => {
  it("lists the stored stages with locked keys", () => {
    renderDialog();
    expect(keys()).toEqual(["core", "heritage", "sunset"]);
    for (const k of screen.getAllByLabelText(/^Key/)) expect(k).toBeDisabled();
  });

  it("says a type without its own stages uses the built-in phases", () => {
    renderDialog(BARE);
    expect(screen.getByText("Built-in phases")).toBeInTheDocument();
    expect(screen.queryAllByLabelText(/^Key/)).toHaveLength(0);
  });

  it("loads the radar preset and saves it in order with semantics", async () => {
    const { user, onSaved, onClose } = renderDialog(BARE);
    await user.click(screen.getByRole("button", { name: "Load Evaluating → Discontinued" }));
    expect(keys()).toEqual(RADAR_PRESET.map((s) => s.key));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchBodies("/metamodel/types/Interface")).toHaveLength(1));
    const body = patchBodies("/metamodel/types/Interface")[0];
    expect(body.lifecycle_config.stages.map((s) => [s.key, s.semantic])).toEqual(
      RADAR_PRESET.map((s) => [s.key, s.semantic]),
    );
    expect(body.lifecycle_config.stages[0]).toEqual({
      key: "evaluating",
      label: "Evaluating",
      color: "#9e9e9e",
      semantic: "pre_operational",
    });
    expect(body).not.toHaveProperty("lifecycle_reassign");
    expect(onSaved).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("keeps existing keys locked when a preset is loaded over them", async () => {
    const { user } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Load Evaluating → Discontinued" }));
    const inputs = screen.getAllByLabelText(/^Key/) as HTMLInputElement[];
    expect(inputs.filter((i) => i.disabled).map((i) => i.value)).toEqual([
      "core",
      "heritage",
      "sunset",
    ]);
  });

  it("reorders, relabels and changes the meaning of a stage", async () => {
    const { user } = renderDialog();
    await user.click(screen.getAllByRole("button", { name: "Move down" })[0]);
    expect(keys()).toEqual(["heritage", "core", "sunset"]);
    await user.click(screen.getAllByRole("button", { name: "Move up" })[2]);
    expect(keys()).toEqual(["heritage", "sunset", "core"]);
    const label = screen.getAllByLabelText(/^Label/)[0];
    await user.clear(label);
    await user.type(label, "Legacy");
    fireEvent.mouseDown(screen.getAllByRole("combobox", { name: "Meaning" })[0]);
    await user.click(within(await screen.findByRole("listbox")).getByText("Retiring"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchBodies()).toHaveLength(1));
    const stages = patchBodies()[0].lifecycle_config.stages;
    expect(stages.map((s) => s.key)).toEqual(["heritage", "sunset", "core"]);
    expect(stages[0]).toMatchObject({ label: "Legacy", semantic: "retiring" });
  });

  it("does not move the first stage up or the last down", () => {
    renderDialog();
    expect(screen.getAllByRole("button", { name: "Move up" })[0]).toBeDisabled();
    expect(screen.getAllByRole("button", { name: "Move down" })[2]).toBeDisabled();
  });

  it("blocks saving a new stage without a key or label, and duplicates", async () => {
    const { user } = renderDialog();
    await user.click(screen.getByRole("button", { name: /Add stage/ }));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await user.type(screen.getAllByLabelText(/^Key/)[3], "core");
    await user.type(screen.getAllByLabelText(/^Label/)[3], "Core again");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await user.type(screen.getAllByLabelText(/^Key/)[3], "Two");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("clearing the list saves an empty vocabulary", async () => {
    const { user } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Clear" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchBodies()).toHaveLength(1));
    expect(patchBodies()[0].lifecycle_config.stages).toEqual([]);
  });

  it("asks where the cards go when a removed stage is in use, then saves the reassignment", async () => {
    mockApi.fail("patch", PATH, 400, {
      code: "lifecycle_stage_in_use",
      message: "in use",
      in_use: { heritage: 3 },
      valid_stages: ["core", "sunset"],
    });
    const { user, onSaved } = renderDialog();
    await user.click(screen.getAllByRole("button", { name: "Delete" })[1]);
    expect(keys()).toEqual(["core", "sunset"]);
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("heritage (cards: 3)")).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
    const confirm = screen.getByRole("button", { name: "Reassign and save" });
    expect(confirm).toBeDisabled();

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Move to" }));
    await user.click(within(await screen.findByRole("listbox")).getByText("sunset"));
    mockApi.on("patch", PATH, {});
    await user.click(confirm);
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(patchBodies().slice(-1)[0].lifecycle_reassign).toEqual({ heritage: "sunset" });
  });

  it("shows any other server refusal as an error", async () => {
    mockApi.fail("patch", PATH, 400, { code: "lifecycle_reassign_conflict", message: "One would be lost" });
    const { user, onClose } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("One would be lost")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("shows a plain-text refusal too", async () => {
    mockApi.fail("patch", PATH, 400, "Duplicate lifecycle stage key 'core'");
    const { user } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Duplicate lifecycle stage key 'core'")).toBeInTheDocument();
  });
});
