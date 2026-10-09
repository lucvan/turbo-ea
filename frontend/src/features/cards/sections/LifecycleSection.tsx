import { useState, useEffect } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import { useTheme } from "@mui/material/styles";
import { useTranslation } from "react-i18next";
import { DateField } from "@/components/DateField";
import MaterialSymbol from "@/components/MaterialSymbol";
import { PHASE_ICONS } from "@/components/LifecycleBadge";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Chip from "@mui/material/Chip";
import { stageOrderIssues, datedStage } from "@/lib/lifecycleStages";
import { useLifecycleStages } from "@/hooks/useLifecycleStages";
import { useDateFormat } from "@/hooks/useDateFormat";
import { todayIsoDate } from "@/lib/dates";
import { useSyncedExpanded } from "@/hooks/useSyncedExpanded";
import type { Card } from "@/types";

const PHASE_PALETTE: Record<string, string> = {
  plan: "#9e9e9e",
  phaseIn: "#1976d2",
  active: "#2e7d32",
  phaseOut: "#ed6c02",
  endOfLife: "#c62828",
};

/** What a caller wants said about one phase, beyond its date. */
export type PhaseAnnotation = {
  /** Rendered under the phase, in both the timeline and the edit row. */
  note?: React.ReactNode;
  /** Marks the phase as differing from some baseline the caller knows about. */
  highlighted?: boolean;
};

// ── Section: Lifecycle ──────────────────────────────────────────
function LifecycleSection({
  card,
  onSave,
  canEdit = true,
  initialExpanded = true,
  onDirtyChange,
  phaseAnnotation,
}: {
  card: Card;
  onSave: (u: Record<string, unknown>) => Promise<void>;
  canEdit?: boolean;
  initialExpanded?: boolean;
  onDirtyChange?: (dirty: boolean) => void;
  /**
   * Optional per-phase annotation. A caller that knows something this
   * component cannot — that a date differs from a stored baseline, say —
   * marks the phase and hangs its own control under it. Deliberately generic:
   * the component renders what it is given and knows nothing about why.
   */
  phaseAnnotation?: (phase: string) => PhaseAnnotation | null | undefined;
}) {
  const { t } = useTranslation(["cards", "common"]);
  const theme = useTheme();
  const { formatDate } = useDateFormat();
  // The card type's stages: its own vocabulary, or the built-in phases. Every
  // per-stage lookup below goes through these four, so the section renders any
  // vocabulary the same way it always rendered the five phases.
  const { stages, customStages, stageLabel } = useLifecycleStages();
  const stageDefs = stages(card.type);
  const isCustom = !!customStages(card.type);
  const PHASES = stageDefs.map((s) => s.key);
  const phaseLabels: Record<string, string> = Object.fromEntries(
    PHASES.map((key) => [key, stageLabel(card.type, key)]),
  );
  // Built-in phases keep their shipped palette; a custom stage uses its own colour.
  const paletteOf = (key: string) =>
    (isCustom ? undefined : PHASE_PALETTE[key]) ??
    stageDefs.find((s) => s.key === key)?.color ??
    "#9e9e9e";
  const [expanded, setExpanded] = useSyncedExpanded(initialExpanded);
  const [editing, setEditing] = useState(false);
  const [lifecycle, setLifecycle] = useState<Record<string, string>>(
    card.lifecycle || {}
  );
  // The explicit current stage — "" is "not stated", leaving it to the dates.
  const [stage, setStage] = useState<string>(card.lifecycle_stage || "");

  // Re-sync the draft from the card prop only while NOT editing, so saving
  // another section (which replaces the whole card object in the parent) can't
  // clobber this section's in-progress draft (issue #843).
  useEffect(() => {
    if (!editing) {
      setLifecycle(card.lifecycle || {});
      setStage(card.lifecycle_stage || "");
    }
  }, [card.lifecycle, card.lifecycle_stage, editing]);

  // Report unsaved-changes state up so the page can warn on navigation (#843).
  const dirty =
    editing &&
    (JSON.stringify(lifecycle) !== JSON.stringify(card.lifecycle || {}) ||
      stage !== (card.lifecycle_stage || ""));
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  // Advisory only: a phase dated after one that should follow it is flagged,
  // never refused — real lifecycles do slip, and the save stays the user's call.
  const orderIssues = stageOrderIssues(lifecycle, stageDefs);
  const orderWarning = (phase: string) => {
    const later = orderIssues[phase];
    if (!later) return null;
    return t("lifecycle.orderWarning", {
      phase: phaseLabels[later],
      date: formatDate(lifecycle[later]),
    });
  };

  const save = async () => {
    // The stage rides along only when it moved, so a dates-only edit sends
    // exactly what it always sent.
    await onSave(
      stage !== (card.lifecycle_stage || "")
        ? { lifecycle, lifecycle_stage: stage || null }
        : { lifecycle },
    );
    setEditing(false);
  };

  return (
    <Accordion expanded={expanded} onChange={(_, v) => setExpanded(v)} disableGutters>
      <AccordionSummary expandIcon={<MaterialSymbol icon="expand_more" size={20} />}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flex: 1 }}>
          <MaterialSymbol icon="timeline" size={20} />
          <Typography fontWeight={600}>{t("lifecycle.title")}</Typography>
        </Box>
        {!editing && canEdit && (
          <IconButton
            size="small"
            onClick={(e) => {
              e.stopPropagation();
              setEditing(true);
            }}
          >
            <MaterialSymbol icon="edit" size={16} />
          </IconButton>
        )}
      </AccordionSummary>
      <AccordionDetails>
        {/* Timeline visualization */}
        <Box
          sx={{
            position: "relative",
            px: 1,
            pt: 3,
            pb: 1,
            mb: 2,
            // The track / fill / dots below stack 0 - 1 - 2 against each other and
            // nothing else. `position: relative` alone does not contain them, so
            // without this they resolve against whatever ancestor stacking context
            // happens to exist and out-paint it -- the card side panel's sticky
            // header (zIndex 1) lost to the dots at 2 and to the fill at 1 on DOM
            // order. `isolation` contains them without altering paint order the
            // way a `zIndex` here would.
            isolation: "isolate",
          }}
        >
          {/* Connecting track behind the dots */}
          <Box
            sx={{
              position: "absolute",
              left: `calc(${100 / (PHASES.length * 2)}% + 8px)`,
              right: `calc(${100 / (PHASES.length * 2)}% + 8px)`,
              top: 36,
              height: 6,
              borderRadius: 3,
              bgcolor: theme.palette.action.hover,
              zIndex: 0,
            }}
          />
          {(() => {
            const now = todayIsoDate();
            // Determine current phase index (latest phase whose date has passed)
            let currentIdx = stage ? PHASES.indexOf(stage) : -1;
            for (let i = PHASES.length - 1; currentIdx < 0 && !stage && i >= 0; i--) {
              const d = lifecycle[PHASES[i]];
              if (d && d <= now) {
                currentIdx = i;
              }
            }
            if (currentIdx < 0) return null;
            // Progress fill goes from start dot to the current dot, stopping
            // at each reached phase color so the gradient walks through every
            // phase the card has been through (e.g. grey → blue → green → orange).
            const fillLeftPct = 100 / (PHASES.length * 2);
            const fillRightPct =
              100 - ((currentIdx * 2 + 1) * 100) / (PHASES.length * 2);
            let gradient: string;
            if (currentIdx === 0) {
              // Single phase reached — fill is zero-width, just render the colour.
              gradient = paletteOf(PHASES[0]);
            } else {
              const stops = PHASES.slice(0, currentIdx + 1)
                .map((phase, i) => {
                  const pct = (i / currentIdx) * 100;
                  return `${paletteOf(phase)} ${pct.toFixed(2)}%`;
                })
                .join(", ");
              gradient = `linear-gradient(90deg, ${stops})`;
            }
            return (
              <Box
                sx={{
                  position: "absolute",
                  left: `calc(${fillLeftPct}% + 8px)`,
                  right: `calc(${fillRightPct}% + 8px)`,
                  top: 36,
                  height: 6,
                  borderRadius: 3,
                  background: gradient,
                  zIndex: 1,
                  transition: "all 0.3s ease",
                }}
              />
            );
          })()}
          <Box
            sx={{
              display: "flex",
              alignItems: "flex-start",
              position: "relative",
              zIndex: 2,
            }}
          >
            {PHASES.map((phase, i) => {
              const date = lifecycle[phase];
              const now = todayIsoDate();
              // With an explicit stage the timeline follows it; the dates stay
              // on show underneath as history and plans.
              const stageIdx = stage ? PHASES.indexOf(stage) : -1;
              const isPast = stage
                ? i < stageIdx
                : i < PHASES.length - 1 &&
                  PHASES.slice(i + 1).some(
                    (p) => lifecycle[p] && lifecycle[p]! <= now,
                  );
              const isCurrent = stage
                ? i === stageIdx
                : !!date && date <= now && !isPast;
              const isReached = isCurrent || isPast;
              const phaseColor = paletteOf(phase);
              const dotBg = isReached ? phaseColor : theme.palette.background.paper;
              const dotBorder = isReached
                ? phaseColor
                : theme.palette.action.disabled;
              const iconColor = isReached
                ? "#fff"
                : theme.palette.text.disabled;
              const annotation = phaseAnnotation?.(phase);
              const warning = orderWarning(phase);
              return (
                <Box
                  key={phase}
                  sx={{
                    flex: 1,
                    textAlign: "center",
                    position: "relative",
                  }}
                >
                  <Box
                    sx={{
                      width: 28,
                      height: 28,
                      borderRadius: "50%",
                      bgcolor: dotBg,
                      border: `2px solid ${dotBorder}`,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      mx: "auto",
                      boxShadow: isCurrent
                        ? `0 0 0 4px ${phaseColor}33`
                        : "none",
                      transition: "all 0.2s ease",
                    }}
                  >
                    <MaterialSymbol
                      icon={PHASE_ICONS[phase] || "circle"}
                      size={16}
                      color={iconColor}
                    />
                  </Box>
                  <Typography
                    variant="caption"
                    display="block"
                    sx={{
                      mt: 1,
                      fontWeight: isCurrent ? 700 : 500,
                      color: isReached ? "text.primary" : "text.secondary",
                      lineHeight: 1.2,
                    }}
                  >
                    {phaseLabels[phase]}
                  </Typography>
                  <Typography
                    variant="caption"
                    color={annotation?.highlighted ? "primary.main" : "text.secondary"}
                    sx={{
                      fontSize: "0.7rem",
                      fontWeight: annotation?.highlighted ? 700 : undefined,
                    }}
                  >
                    {date ? formatDate(date) : "—"}
                  </Typography>
                  {warning && (
                    <Tooltip title={warning}>
                      <Box
                        component="span"
                        role="img"
                        aria-label={warning}
                        sx={{ display: "inline-flex", ml: 0.5, verticalAlign: "middle" }}
                      >
                        <MaterialSymbol icon="warning" size={14} color={theme.palette.warning.main} />
                      </Box>
                    </Tooltip>
                  )}
                  {annotation?.note}
                </Box>
              );
            })}
          </Box>
        </Box>
        {!editing && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1, flexWrap: "wrap" }}>
            <Typography variant="body2" color="text.secondary">
              {t("lifecycle.currentStage")}
            </Typography>
            {(() => {
              const derived = datedStage(lifecycle, stageDefs);
              const key = stage || derived;
              if (!key) {
                return (
                  <Typography variant="body2" color="text.secondary" fontStyle="italic">
                    {t("lifecycle.unknown")}
                  </Typography>
                );
              }
              return (
                <>
                  <Chip
                    size="small"
                    variant="outlined"
                    label={phaseLabels[key] ?? key}
                    sx={{ borderColor: paletteOf(key) }}
                  />
                  <Typography variant="caption" color="text.secondary">
                    {stage ? t("lifecycle.stated") : t("lifecycle.fromDates")}
                  </Typography>
                  {stage && derived && derived !== stage && (
                    <Tooltip
                      title={t("lifecycle.datesDisagree", { stage: phaseLabels[derived] ?? derived })}
                    >
                      <Box component="span" sx={{ display: "inline-flex" }}>
                        <MaterialSymbol icon="warning" size={14} color={theme.palette.warning.main} />
                      </Box>
                    </Tooltip>
                  )}
                </>
              );
            })()}
          </Box>
        )}
        {editing && (
          <Box>
            <TextField
              select
              size="small"
              label={t("lifecycle.currentStage")}
              value={stage}
              onChange={(e) => setStage(e.target.value)}
              helperText={t("lifecycle.currentStageHelp")}
              sx={{ minWidth: 260, mb: 2 }}
            >
              <MenuItem value="">
                <em>{t("lifecycle.notStated")}</em>
              </MenuItem>
              {/* A stored stage the vocabulary no longer defines stays selectable,
                  so opening the editor never silently changes the card. */}
              {stage && !PHASES.includes(stage) && <MenuItem value={stage}>{stage}</MenuItem>}
              {PHASES.map((key) => (
                <MenuItem key={key} value={key}>
                  {phaseLabels[key]}
                </MenuItem>
              ))}
            </TextField>
            <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap", mb: 2 }}>
              {PHASES.map((phase) => {
                const annotation = phaseAnnotation?.(phase);
                const warning = orderWarning(phase);
                return (
                  <Box key={phase} sx={{ maxWidth: 170 }}>
                    <DateField
                      label={phaseLabels[phase]}
                      size="small"
                      value={lifecycle[phase] || ""}
                      onChange={(v) =>
                        setLifecycle({ ...lifecycle, [phase]: v })
                      }
                      helperText={warning ?? undefined}
                      slotProps={
                        warning
                          ? { formHelperText: { sx: { color: "warning.main", mx: 0 } } }
                          : undefined
                      }
                      sx={{
                        width: 170,
                        ...(annotation?.highlighted
                          ? {
                              "& .MuiOutlinedInput-notchedOutline": {
                                borderColor: "primary.main",
                              },
                              "& .MuiInputLabel-root": { color: "primary.main" },
                            }
                          : null),
                      }}
                    />
                    {annotation?.note}
                  </Box>
                );
              })}
            </Box>
            <Box sx={{ display: "flex", gap: 1, justifyContent: "flex-end" }}>
              <Button
                size="small"
                onClick={() => {
                  setLifecycle(card.lifecycle || {});
                  setStage(card.lifecycle_stage || "");
                  setEditing(false);
                }}
              >
                {t("common:actions.cancel")}
              </Button>
              <Button size="small" variant="contained" onClick={save}>
                {t("common:actions.save")}
              </Button>
            </Box>
          </Box>
        )}
      </AccordionDetails>
    </Accordion>
  );
}

export default LifecycleSection;
