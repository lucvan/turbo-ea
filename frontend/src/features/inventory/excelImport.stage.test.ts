/**
 * The explicit lifecycle stage in the Excel import: a filled `lifecycle_stage`
 * cell lands on the card with no date, a blank one stays unknown, and a type's
 * own stages get their own dated columns.
 */
import { describe, expect, it } from "vitest";

import { RADAR_PRESET } from "@/lib/lifecycleStages";
import {
  CARD_IDS,
  CARD_TYPES,
  CARDS,
  STAKEHOLDER_ROLES_BY_TYPE,
  TAG_GROUPS,
  USERS,
} from "@/test/fixtures/metamodel";
import type { Card } from "@/types";

import { validateImport } from "./excelImport";

type Row = Record<string, unknown>;

const TYPES = CARD_TYPES.map((ct) =>
  ct.key === "Application" ? { ...ct, lifecycle_config: { stages: RADAR_PRESET } } : ct,
);

const run = (rows: Row[], existing: Card[] = CARDS) =>
  validateImport(rows, existing, TYPES, undefined, TAG_GROUPS, {}, USERS, STAKEHOLDER_ROLES_BY_TYPE, {});

const app = (over: Row = {}): Row => ({ type: "Application", name: "Brand New App", ...over });

describe("lifecycle_stage column", () => {
  it("is a recognised column", () => {
    const r = run([app({ lifecycle_stage: "" })]);
    expect(r.warnings.filter((w) => w.column === "lifecycle_stage")).toEqual([]);
  });

  it("sets the stage on a new card without any date", () => {
    const r = run([app({ lifecycle_stage: "heritage" })]);
    expect(r.errors).toEqual([]);
    expect(r.creates[0].data.lifecycle_stage).toBe("heritage");
    expect(r.creates[0].data).not.toHaveProperty("lifecycle");
  });

  it("leaves a blank stage unknown rather than defaulting it", () => {
    for (const blank of ["", "   ", undefined, null]) {
      const r = run([app({ lifecycle_stage: blank })]);
      expect(r.creates[0].data).not.toHaveProperty("lifecycle_stage");
      expect(r.creates[0].data).not.toHaveProperty("lifecycle");
    }
  });

  it("patches the stage on an existing card only when it differs", () => {
    const erp = CARDS.find((c) => c.id === CARD_IDS.erp)!;
    const row = { id: erp.id, type: erp.type, name: erp.name };
    const changed = run([{ ...row, lifecycle_stage: "sunset" }]);
    expect(changed.updates).toHaveLength(1);
    expect(changed.updates[0].data.lifecycle_stage).toBe("sunset");
    expect(changed.updates[0].changes?.lifecycle_stage).toEqual({ old: null, new: "sunset" });

    const existing = CARDS.map((c) => (c.id === erp.id ? { ...c, lifecycle_stage: "sunset" } : c));
    const same = run([{ ...row, lifecycle_stage: "sunset" }], existing);
    expect(same.updates.filter((u) => u.changes?.lifecycle_stage)).toEqual([]);
    const blank = run([{ ...row, lifecycle_stage: "" }], existing);
    expect(blank.updates.filter((u) => u.changes?.lifecycle_stage)).toEqual([]);
  });
});

describe("dated columns for a type's own stages", () => {
  it("reads lifecycle_<stage> columns and recognises them", () => {
    const r = run([app({ lifecycle_core: "2020-01-01", lifecycle_sunset: "2027-06-30" })]);
    expect(r.errors).toEqual([]);
    expect(r.warnings.filter((w) => String(w.column).startsWith("lifecycle_"))).toEqual([]);
    expect(r.creates[0].data.lifecycle).toEqual({ core: "2020-01-01", sunset: "2027-06-30" });
  });

  it("ignores the built-in phase columns on a type with its own stages", () => {
    const r = run([app({ lifecycle_active: "2020-01-01" })]);
    expect(r.creates[0].data).not.toHaveProperty("lifecycle");
  });

  it("still validates the date", () => {
    const r = run([app({ lifecycle_core: "not a date" })]);
    expect(r.errors.map((e) => e.column)).toEqual(["lifecycle_core"]);
  });
});
