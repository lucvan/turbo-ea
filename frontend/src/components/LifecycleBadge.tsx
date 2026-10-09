import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import { useTheme } from "@mui/material/styles";
import { useTranslation } from "react-i18next";
import MaterialSymbol from "@/components/MaterialSymbol";
import { todayIsoDate } from "@/lib/dates";
import { datedStage, findStage } from "@/lib/lifecycleStages";
import { useOptionLabel } from "@/hooks/useResolveLabel";
import type { LifecycleStageDef } from "@/types";

export const PHASE_COLORS: Record<
  string,
  "default" | "primary" | "success" | "warning" | "error"
> = {
  plan: "default",
  phaseIn: "primary",
  active: "success",
  phaseOut: "warning",
  endOfLife: "error",
};

export const PHASE_ICONS: Record<string, string> = {
  plan: "edit_calendar",
  phaseIn: "trending_up",
  active: "check_circle",
  phaseOut: "trending_down",
  endOfLife: "block",
};

/**
 * The lifecycle phase a card is in on a given date.
 *
 * `asOfMs` defaults to now. Pass it when rendering a time-travelled view, so the
 * phase shown is the one at the selected date rather than today's — otherwise a
 * card retired in 2030 reads as "active" while you are looking at 2031.
 */
export function getCurrentPhase(
  lifecycle?: Record<string, string>,
  asOfMs?: number,
): string | null {
  if (!lifecycle) return null;
  // The slider's instant is built from a local `Date`, so its local
  // calendar day is the one to compare against — `toISOString()` would read
  // the UTC day and flip the phase early west of UTC (#1016).
  const now = todayIsoDate(new Date(asOfMs ?? Date.now()));
  const phases = ["endOfLife", "phaseOut", "active", "phaseIn", "plan"] as const;
  for (const phase of phases) {
    if (lifecycle[phase] && lifecycle[phase] <= now) return phase;
  }
  if (lifecycle.plan && lifecycle.plan > now) return "plan";
  return null;
}

interface Props {
  lifecycle?: Record<string, string>;
  /** The card's explicit current stage. Wins over the dates when set. */
  stage?: string | null;
  /** The card type's own stage vocabulary; omit for the built-in phases. */
  stages?: LifecycleStageDef[];
  size?: "small" | "medium";
}

export default function LifecycleBadge({ lifecycle, stage, stages, size = "small" }: Props) {
  const { t } = useTranslation("common");
  const theme = useTheme();
  const optLabel = useOptionLabel();
  if (stages && stages.length > 0) {
    const key = stage || datedStage(lifecycle, stages);
    if (!key) return null;
    const def = findStage(stages, key);
    // A stage the vocabulary no longer defines still renders, by its key, in
    // the neutral colour — the card holds it until someone changes it.
    const color = def?.color ?? theme.palette.text.secondary;
    return (
      <Chip
        size={size}
        label={def ? optLabel(def) : key}
        variant="outlined"
        sx={{ borderColor: color }}
        icon={
          <Box
            sx={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              bgcolor: color,
              flexShrink: 0,
              ml: "8px !important",
            }}
          />
        }
      />
    );
  }
  const phase = stage || getCurrentPhase(lifecycle);
  if (!phase) return null;
  const chipColor = PHASE_COLORS[phase] || "default";
  const dotColor =
    chipColor === "default"
      ? theme.palette.text.secondary
      : theme.palette[chipColor].main;
  const iconName = PHASE_ICONS[phase];
  return (
    <Chip
      size={size}
      label={t(`lifecycle.${phase}`) || phase}
      color={chipColor}
      variant="outlined"
      icon={
        iconName ? (
          <Box
            sx={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              color: dotColor,
            }}
          >
            <MaterialSymbol icon={iconName} size={size === "small" ? 16 : 18} />
          </Box>
        ) : (
          <Box
            sx={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              bgcolor: dotColor,
              flexShrink: 0,
            }}
          />
        )
      }
    />
  );
}
