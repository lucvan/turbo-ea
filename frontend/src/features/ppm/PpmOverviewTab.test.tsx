/**
 * PpmOverviewTab — the initiative snapshot. Pins the Description panel's
 * heading, which is the shared `common:labels.description` key, so it follows
 * the user's language like the rest of the tab.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import type { Card } from "@/types";

vi.mock("@/api/client", () => import("@/test/apiMock").then((m) => m.apiClientModule()));
vi.mock("@/hooks/useCurrency", () => import("@/test/hooks").then((m) => m.useCurrencyModule()));
vi.mock("@/hooks/useMetamodel", () => import("@/test/hooks").then((m) => m.useMetamodelModule()));

import { mockApi } from "@/test/apiMock";
import { hookState } from "@/test/hooks";
import i18n from "@/i18n";
import PpmOverviewTab from "./PpmOverviewTab";

const CARD = {
  id: "i1",
  type: "Initiative",
  name: "ERP Migration",
  description: "Move finance to the new ERP",
  subtype: null,
  attributes: {},
} as unknown as Card;

function renderTab(card: Card = CARD) {
  render(<PpmOverviewTab card={card} latestReport={null} costLines={[]} budgetLines={[]} />);
}

beforeEach(() => {
  mockApi.reset();
  hookState.reset();
  mockApi.on("get", "/ppm/initiatives/i1/completion", { completion: 40 });
});

describe("PpmOverviewTab — description", () => {
  it("heads the initiative's description", async () => {
    renderTab();
    await screen.findByText("40%");
    const heading = screen.getByText("Description");
    expect(heading.nextElementSibling).toHaveTextContent("Move finance to the new ERP");
  });

  it("heads the description in the user's language", async () => {
    await act(async () => {
      await i18n.changeLanguage("de");
    });
    try {
      renderTab();
      await screen.findByText("40%");
      expect(screen.getByText("Beschreibung").nextElementSibling).toHaveTextContent(
        "Move finance to the new ERP",
      );
      expect(screen.queryByText("Description")).not.toBeInTheDocument();
    } finally {
      await act(async () => {
        await i18n.changeLanguage("en");
      });
    }
  });

  it("leaves the panel out when the initiative has no description", async () => {
    renderTab({ ...CARD, description: null } as unknown as Card);
    await screen.findByText("40%");
    expect(screen.queryByText("Description")).not.toBeInTheDocument();
  });
});
