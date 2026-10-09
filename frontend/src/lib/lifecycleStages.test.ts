import { describe, it, expect } from "vitest";
import {
  DEFAULT_STAGES,
  RADAR_PRESET,
  currentStage,
  customStagesOf,
  datedStage,
  findStage,
  stageOrderIssues,
  stagesOf,
} from "./lifecycleStages";

const NOW = new Date(2026, 5, 1, 12).getTime();
const radarType = { lifecycle_config: { stages: RADAR_PRESET } };

describe("stagesOf", () => {
  it("falls back to the built-in phases", () => {
    expect(stagesOf(undefined).map((s) => s.key)).toEqual([
      "plan",
      "phaseIn",
      "active",
      "phaseOut",
      "endOfLife",
    ]);
    expect(stagesOf({ lifecycle_config: {} })).toBe(DEFAULT_STAGES);
    expect(stagesOf({ lifecycle_config: { stages: [] } })).toBe(DEFAULT_STAGES);
    expect(customStagesOf({ lifecycle_config: { stages: [] } })).toBeUndefined();
    expect(customStagesOf(null)).toBeUndefined();
  });

  it("uses the type's own vocabulary", () => {
    expect(stagesOf(radarType)).toBe(RADAR_PRESET);
    expect(customStagesOf(radarType)).toBe(RADAR_PRESET);
  });

  it("ships the radar preset with Emerging as operational", () => {
    expect(RADAR_PRESET.map((s) => [s.key, s.semantic])).toEqual([
      ["evaluating", "pre_operational"],
      ["emerging", "operational"],
      ["core", "operational"],
      ["heritage", "operational"],
      ["sunset", "retiring"],
      ["discontinued", "retired"],
    ]);
  });
});

describe("findStage", () => {
  it("finds by key and tolerates a missing key", () => {
    expect(findStage(RADAR_PRESET, "core")?.label).toBe("Core");
    expect(findStage(RADAR_PRESET, "active")).toBeUndefined();
    expect(findStage(RADAR_PRESET, null)).toBeUndefined();
    expect(findStage(RADAR_PRESET, "")).toBeUndefined();
  });
});

describe("datedStage", () => {
  it("is null with nothing dated", () => {
    expect(datedStage(undefined, RADAR_PRESET, NOW)).toBeNull();
    expect(datedStage({}, RADAR_PRESET, NOW)).toBeNull();
    expect(datedStage({ core: "" }, RADAR_PRESET, NOW)).toBeNull();
  });

  it("takes the most advanced stage whose date has passed", () => {
    const lifecycle = { core: "2020-01-01", sunset: "2025-01-01", discontinued: "2027-01-01" };
    expect(datedStage(lifecycle, RADAR_PRESET, NOW)).toBe("sunset");
  });

  it("counts a date of today as reached and tomorrow as a plan", () => {
    expect(datedStage({ evaluating: "2020-01-01", core: "2026-06-01" }, RADAR_PRESET, NOW)).toBe(
      "core",
    );
    expect(datedStage({ evaluating: "2020-01-01", core: "2026-06-02" }, RADAR_PRESET, NOW)).toBe(
      "evaluating",
    );
  });

  it("falls back to the earliest dated stage when every date is ahead", () => {
    expect(datedStage({ sunset: "2030-01-01", core: "2028-01-01" }, RADAR_PRESET, NOW)).toBe(
      "core",
    );
  });

  it("ignores keys outside the vocabulary", () => {
    expect(datedStage({ active: "2020-01-01" }, RADAR_PRESET, NOW)).toBeNull();
  });

  it("defaults to today", () => {
    expect(datedStage({ core: "2000-01-01", sunset: "2999-01-01" }, RADAR_PRESET)).toBe("core");
  });
});

describe("currentStage", () => {
  it("is unknown when nothing is recorded", () => {
    expect(currentStage({}, RADAR_PRESET)).toBeNull();
    expect(currentStage({ lifecycle: {}, lifecycle_stage: null }, RADAR_PRESET)).toBeNull();
    expect(currentStage({ lifecycle_stage: "" }, RADAR_PRESET)).toBeNull();
  });

  it("takes an explicit stage with no date", () => {
    expect(currentStage({ lifecycle_stage: "heritage" }, RADAR_PRESET)).toBe("heritage");
  });

  it("lets the explicit stage win over the dates", () => {
    const card = { lifecycle: { sunset: "2020-01-01" }, lifecycle_stage: "core" };
    expect(currentStage(card, RADAR_PRESET)).toBe("core");
  });

  it("goes by the dates in a time-travelled view", () => {
    const card = { lifecycle: { sunset: "2020-01-01" }, lifecycle_stage: "core" };
    expect(currentStage(card, RADAR_PRESET, NOW)).toBe("sunset");
    expect(currentStage({ lifecycle_stage: "core" }, RADAR_PRESET, NOW)).toBeNull();
  });
});

describe("stageOrderIssues", () => {
  it("is empty for ordered or missing dates", () => {
    expect(stageOrderIssues(undefined, RADAR_PRESET)).toEqual({});
    expect(stageOrderIssues({ core: "2020-01-01", sunset: "2021-01-01" }, RADAR_PRESET)).toEqual(
      {},
    );
    expect(stageOrderIssues({ core: "2020-01-01", sunset: "2020-01-01" }, RADAR_PRESET)).toEqual(
      {},
    );
  });

  it("flags a stage dated after one that follows it, across a blank", () => {
    expect(
      stageOrderIssues({ emerging: "2025-01-01", discontinued: "2024-01-01" }, RADAR_PRESET),
    ).toEqual({ emerging: "discontinued" });
  });

  it("points at the first later stage and skips malformed dates", () => {
    expect(
      stageOrderIssues(
        { core: "2025-01-01", heritage: "soon", sunset: "2024-06-01", discontinued: "2024-01-01" },
        RADAR_PRESET,
      ),
    ).toEqual({ core: "sunset", sunset: "discontinued" });
  });
});
