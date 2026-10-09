import { describe, it, expect } from "vitest";
import { cardCreatePayload } from "./cardSync";

const pending = { type: "Application", name: "Draft App" };

describe("cardCreatePayload", () => {
  it("sends the type and the name for a card created without a description", () => {
    expect(cardCreatePayload(pending)).toEqual({ type: "Application", name: "Draft App" });
    expect(cardCreatePayload(pending)).not.toHaveProperty("description");
  });

  it("carries the description typed in the Create-card dialog (#1210)", () => {
    expect(cardCreatePayload({ ...pending, description: "Hosts the ERP" })).toEqual({
      type: "Application",
      name: "Draft App",
      description: "Hosts the ERP",
    });
  });

  it("trims the description and omits a blank one", () => {
    // `CardCreate.description` is `str | None`: a blank is "none", not "".
    expect(cardCreatePayload({ ...pending, description: "  notes  " })).toMatchObject({
      description: "notes",
    });
    expect(cardCreatePayload({ ...pending, description: "" })).not.toHaveProperty("description");
    expect(cardCreatePayload({ ...pending, description: "   " })).not.toHaveProperty(
      "description",
    );
  });

  it("forwards nothing else from a scanned pending card", () => {
    // The temp id and the cell id are canvas bookkeeping; the inventory must
    // never see them.
    const scanned = { cellId: "pfs-1", tempId: "pending-x", ...pending, description: "d" };
    expect(Object.keys(cardCreatePayload(scanned)).sort()).toEqual([
      "description",
      "name",
      "type",
    ]);
  });
});
