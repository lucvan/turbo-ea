/**
 * Configurable lifecycle stages, per card type.
 *
 * A card type either uses the built-in five phases (`PHASES`, labelled through
 * i18n) or defines its own ordered vocabulary in `lifecycle_config.stages`.
 * Two facts are recorded per card and kept apart:
 *
 *   - `lifecycle`        stage key → ISO date: dated history and planned
 *                        transitions. A future date is a plan.
 *   - `lifecycle_stage`  the explicit current stage, recorded without a date.
 *
 * The current stage is the explicit one when stated, otherwise the one the
 * dates give. A card with neither is unknown, never assumed operational.
 *
 * A leaf on purpose, like `lifecyclePhases`: it imports types only.
 */
import type { CardType, LifecycleStageDef, StageSemantic } from "@/types";
import { todayIsoDate } from "@/lib/dates";

export const STAGE_SEMANTICS: StageSemantic[] = [
  "pre_operational",
  "operational",
  "retiring",
  "retired",
];

/** The built-in model. Keys, order and colours match what has always shipped. */
export const DEFAULT_STAGES: LifecycleStageDef[] = [
  { key: "plan", label: "Plan", color: "#9e9e9e", semantic: "pre_operational" },
  { key: "phaseIn", label: "Phase In", color: "#1976d2", semantic: "pre_operational" },
  { key: "active", label: "Active", color: "#2e7d32", semantic: "operational" },
  { key: "phaseOut", label: "Phase Out", color: "#ed6c02", semantic: "retiring" },
  { key: "endOfLife", label: "End of Life", color: "#c62828", semantic: "retired" },
];

/** A ready-made vocabulary an admin can load into the editor. */
export const RADAR_PRESET: LifecycleStageDef[] = [
  { key: "evaluating", label: "Evaluating", color: "#9e9e9e", semantic: "pre_operational" },
  { key: "emerging", label: "Emerging", color: "#1976d2", semantic: "operational" },
  { key: "core", label: "Core", color: "#2e7d32", semantic: "operational" },
  { key: "heritage", label: "Heritage", color: "#8d6e63", semantic: "operational" },
  { key: "sunset", label: "Sunset", color: "#ed6c02", semantic: "retiring" },
  { key: "discontinued", label: "Discontinued", color: "#c62828", semantic: "retired" },
];

type TypeLike = Pick<CardType, "lifecycle_config"> | null | undefined;

/** The type's own vocabulary, or `undefined` when it uses the built-in model. */
export function customStagesOf(type: TypeLike): LifecycleStageDef[] | undefined {
  const stages = type?.lifecycle_config?.stages;
  return stages && stages.length > 0 ? stages : undefined;
}

/** The ordered stages in force for a type. */
export function stagesOf(type: TypeLike): LifecycleStageDef[] {
  return customStagesOf(type) ?? DEFAULT_STAGES;
}

export function findStage(
  stages: LifecycleStageDef[],
  key: string | null | undefined,
): LifecycleStageDef | undefined {
  return key ? stages.find((s) => s.key === key) : undefined;
}

type Lifecycle = Record<string, string | null | undefined> | null | undefined;

/**
 * The stage the dates alone put a card in: the most advanced stage whose date
 * has passed, else the earliest dated stage when every date is still ahead.
 */
export function datedStage(
  lifecycle: Lifecycle,
  stages: LifecycleStageDef[],
  asOfMs?: number,
): string | null {
  if (!lifecycle) return null;
  // Local calendar day, not UTC — see `getCurrentPhase` (#1016).
  const now = todayIsoDate(new Date(asOfMs ?? Date.now()));
  for (let i = stages.length - 1; i >= 0; i--) {
    const d = lifecycle[stages[i].key];
    if (d && d <= now) return stages[i].key;
  }
  for (const s of stages) {
    if (lifecycle[s.key]) return s.key;
  }
  return null;
}

/**
 * A card's current stage, or `null` when it is unknown.
 *
 * Pass `asOfMs` only for a time-travelled view; the explicit stage describes
 * today, so it is ignored there and the dates decide.
 */
export function currentStage(
  card: { lifecycle?: Lifecycle; lifecycle_stage?: string | null },
  stages: LifecycleStageDef[],
  asOfMs?: number,
): string | null {
  if (asOfMs === undefined && card.lifecycle_stage) return card.lifecycle_stage;
  return datedStage(card.lifecycle, stages, asOfMs);
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Stages dated after a stage that should follow them. Each out-of-order stage
 * maps to the first later stage whose date is earlier than its own. Advisory
 * only, exactly like `lifecycleOrderIssues` for the built-in phases.
 */
export function stageOrderIssues(
  lifecycle: Lifecycle,
  stages: LifecycleStageDef[],
): Record<string, string> {
  const issues: Record<string, string> = {};
  if (!lifecycle) return issues;
  const dateOf = (key: string) => {
    const v = lifecycle[key];
    return typeof v === "string" && ISO_DATE.test(v) ? v : null;
  };
  stages.forEach((stage, i) => {
    const date = dateOf(stage.key);
    if (!date) return;
    const later = stages.slice(i + 1).find((s) => {
      const d = dateOf(s.key);
      return d !== null && d < date;
    });
    if (later) issues[stage.key] = later.key;
  });
  return issues;
}
