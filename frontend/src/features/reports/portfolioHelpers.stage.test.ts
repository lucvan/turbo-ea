/**
 * Reports receive every card's lifecycle under the built-in phase names. Where
 * a card's stated stage says something no date does, the server flags it under
 * `STAGE_SEMANTIC_KEY`, and the alive-checks honour it without a date.
 */
import { describe, expect, it } from "vitest";
import {
  STAGE_SEMANTIC_KEY,
  hasStartedByDate,
  isAliveAtDate,
  isRetiredByDate,
} from "./portfolioHelpers";

const AT = new Date(2026, 5, 1).getTime();

describe("stated stage without a date", () => {
  it("uses the reserved key the server writes", () => {
    expect(STAGE_SEMANTIC_KEY).toBe("_semantic");
  });

  it("treats a card stated as retired as retired", () => {
    const lifecycle = { _semantic: "retired" };
    expect(isRetiredByDate(lifecycle, AT)).toBe(true);
    expect(isAliveAtDate(lifecycle, AT)).toBe(false);
    expect(hasStartedByDate(lifecycle, AT)).toBe(true);
  });

  it("lets a retirement date decide when there is one", () => {
    expect(isRetiredByDate({ endOfLife: "2030-01-01", _semantic: "retired" }, AT)).toBe(false);
    expect(isRetiredByDate({ endOfLife: "2020-01-01" }, AT)).toBe(true);
  });

  it("treats a card stated as pre-operational as not started", () => {
    const lifecycle = { _semantic: "pre_operational" };
    expect(hasStartedByDate(lifecycle, AT)).toBe(false);
    expect(isAliveAtDate(lifecycle, AT)).toBe(false);
    expect(isRetiredByDate(lifecycle, AT)).toBe(false);
  });

  it("lets a go-live date decide when there is one", () => {
    expect(hasStartedByDate({ active: "2020-01-01", _semantic: "pre_operational" }, AT)).toBe(true);
  });

  it("leaves undated cards with no flag alive, as before", () => {
    expect(isAliveAtDate({}, AT)).toBe(true);
    expect(isAliveAtDate(undefined, AT)).toBe(true);
    expect(isAliveAtDate({ _semantic: "operational" }, AT)).toBe(true);
  });
});
