/**
 * VendorField regressions: the linked-Provider chip belongs to the card it was
 * looked up for, and in the create-a-Provider flow it appears only once the new
 * Provider is actually linked to the card.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";

vi.mock("@/api/client", () => import("@/test/apiMock").then((m) => m.apiClientModule()));
vi.mock("@/hooks/useMetamodel", () => import("@/test/hooks").then((m) => m.useMetamodelModule()));

import { mockApi } from "@/test/apiMock";
import { hookState, withMetamodel } from "@/test/hooks";
import { renderWithProviders, wrapWithProviders } from "@/test/render";
import {
  CARD_IDS,
  CARD_TYPES,
  RELATION_TYPES,
  REL_PROVIDER_TO_ITC,
  cardPage,
} from "@/test/fixtures/metamodel";
import type { Relation } from "@/types";
import VendorField from "./VendorField";

const FS_ID = CARD_IDS.postgres;
const OTHER = CARD_IDS.erp;
const RELATIONS_URL = `/relations?card_id=${FS_ID}&type=${REL_PROVIDER_TO_ITC.key}`;
const OTHER_URL = `/relations?card_id=${OTHER}&type=${REL_PROVIDER_TO_ITC.key}`;

function linkedTo(cardId: string, provider: { id: string; name: string }): Relation[] {
  return [
    {
      id: `rel-${provider.id}`,
      type: REL_PROVIDER_TO_ITC.key,
      source_id: provider.id,
      target_id: cardId,
      source: { id: provider.id, type: "Provider", name: provider.name },
      target: { id: cardId, type: "ITComponent", name: "Some card" },
    },
  ];
}

const chip = (name: string) => screen.queryByText(name, { selector: ".MuiChip-label" });

function field(fsId?: string) {
  return <VendorField value="" onChange={() => {}} cardTypeKey="ITComponent" fsId={fsId} />;
}

beforeEach(() => {
  hookState.reset();
  withMetamodel(CARD_TYPES, RELATION_TYPES);
  mockApi.reset();
  mockApi.on("get", "/cards?*", cardPage([]));
  mockApi.on("delete", "/relations/*", {});
});

describe("VendorField — the chip follows the card", () => {
  it("drops the previous card's Provider when the next card has none", async () => {
    mockApi.on("get", RELATIONS_URL, linkedTo(FS_ID, { id: "p-globex", name: "Globex" }));
    mockApi.on("get", OTHER_URL, []);
    const { rerender } = renderWithProviders(field(FS_ID));
    expect(await screen.findByText("Globex", { selector: ".MuiChip-label" })).toBeInTheDocument();

    rerender(wrapWithProviders(field(OTHER)));
    await waitFor(() => expect(mockApi.callsOf("get", OTHER_URL)).toHaveLength(1));
    await waitFor(() => expect(chip("Globex")).not.toBeInTheDocument());
  });

  it("does not show the previous card's Provider while the next card's lookup is pending", async () => {
    mockApi.on("get", RELATIONS_URL, linkedTo(FS_ID, { id: "p-globex", name: "Globex" }));
    mockApi.on("get", OTHER_URL, () => new Promise(() => {}));
    const { rerender } = renderWithProviders(field(FS_ID));
    expect(await screen.findByText("Globex", { selector: ".MuiChip-label" })).toBeInTheDocument();

    rerender(wrapWithProviders(field(OTHER)));
    await waitFor(() => expect(mockApi.callsOf("get", OTHER_URL)).toHaveLength(1));
    expect(chip("Globex")).not.toBeInTheDocument();
  });

  it("shows the next card's own Provider", async () => {
    mockApi.on("get", RELATIONS_URL, linkedTo(FS_ID, { id: "p-globex", name: "Globex" }));
    mockApi.on("get", OTHER_URL, linkedTo(OTHER, { id: "p-acme", name: "Acme Corp" }));
    const { rerender } = renderWithProviders(field(FS_ID));
    expect(await screen.findByText("Globex", { selector: ".MuiChip-label" })).toBeInTheDocument();

    rerender(wrapWithProviders(field(OTHER)));
    expect(await screen.findByText("Acme Corp", { selector: ".MuiChip-label" })).toBeInTheDocument();
    expect(chip("Globex")).not.toBeInTheDocument();
  });
});

describe("VendorField — creating a Provider for a card", () => {
  async function createInitech(user: ReturnType<typeof renderWithProviders>["user"]) {
    await user.type(screen.getByLabelText("Provider"), "Initech");
    await user.click(await screen.findByRole("option", { name: /Create Provider "Initech"/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Create & Link" }));
  }

  it("shows no chip for the new Provider when linking it to the card fails", async () => {
    mockApi.on("post", "/cards", { id: "prov-new", name: "Initech" });
    mockApi.on("get", RELATIONS_URL, []);
    mockApi.fail("post", "/relations", 500);
    const { user } = renderWithProviders(field(FS_ID));
    await createInitech(user);

    expect(await screen.findByRole("alert")).toHaveTextContent("POST /relations failed");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(chip("Initech")).not.toBeInTheDocument();
  });

  it("shows the chip once the new Provider is linked", async () => {
    mockApi.on("post", "/cards", { id: "prov-new", name: "Initech" });
    mockApi.on("post", "/relations", {});
    mockApi.on("get", RELATIONS_URL, () =>
      mockApi.callsOf("post", "/relations").length
        ? linkedTo(FS_ID, { id: "prov-new", name: "Initech" })
        : [],
    );
    const { user } = renderWithProviders(field(FS_ID));
    await createInitech(user);

    expect(await screen.findByText("Initech", { selector: ".MuiChip-label" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
