import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import ColorPicker from "@/components/ColorPicker";
import KeyInput, { isValidKey } from "@/components/KeyInput";
import MaterialSymbol from "@/components/MaterialSymbol";
import { api, ApiError } from "@/api/client";
import { LOCALE_LABELS } from "@/i18n";
import {
  DEFAULT_STAGES,
  RADAR_PRESET,
  STAGE_SEMANTICS,
  customStagesOf,
} from "@/lib/lifecycleStages";
import type { CardType, LifecycleStageDef } from "@/types";
import { DEFAULT_OPTION_COLOR } from "./constants";
import { cleanTranslationMap } from "./helpers";

interface Props {
  open: boolean;
  cardType: CardType | null;
  onClose: () => void;
  onSaved: () => void;
}

type Row = LifecycleStageDef & { _original?: boolean };

/** The server's refusal to drop stages that cards still use. */
interface InUseDetail {
  code: "lifecycle_stage_in_use";
  in_use: Record<string, number>;
  valid_stages: string[];
}

function isInUse(detail: unknown): detail is InUseDetail {
  return (
    typeof detail === "object" &&
    detail !== null &&
    (detail as { code?: unknown }).code === "lifecycle_stage_in_use"
  );
}

function messageOf(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (typeof e.detail === "string") return e.detail;
    const message = (e.detail as { message?: unknown } | null)?.message;
    if (typeof message === "string") return message;
  }
  return e instanceof Error ? e.message : fallback;
}

/**
 * Admin editor for a card type's **lifecycle stages**: an ordered vocabulary of
 * `{key, label, colour, semantic}`. Saving an empty list returns the type to
 * the built-in five phases.
 *
 * A stage's key is what cards store, so it is locked once saved; label, colour,
 * semantic and position are free to change. Removing a stage that cards still
 * use is refused by the server — the dialog then asks where each such stage's
 * cards go and saves again with that reassignment. Nothing is reassigned
 * without the admin choosing the target.
 */
export default function LifecycleStagesDialog({ open, cardType, onClose, onSaved }: Props) {
  const { t, i18n } = useTranslation(["admin", "common", "validation"]);
  const locale = i18n.language;

  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [inUse, setInUse] = useState<InUseDetail | null>(null);
  const [reassign, setReassign] = useState<Record<string, string>>({});

  useEffect(() => {
    if (open && cardType) {
      const cloned: Row[] = JSON.parse(JSON.stringify(customStagesOf(cardType) ?? []));
      for (const r of cloned) r._original = true;
      setRows(cloned);
      setError(null);
      setInUse(null);
      setReassign({});
    }
  }, [open, cardType]);

  const patchRow = (index: number, patch: Partial<Row>) => {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  };

  const setLabelText = (index: number, text: string) => {
    setRows((prev) =>
      prev.map((r, i) =>
        i === index ? { ...r, label: text, translations: { ...r.translations, [locale]: text } } : r,
      ),
    );
  };

  const move = (index: number, delta: number) => {
    setRows((prev) => {
      const target = index + delta;
      if (target < 0 || target >= prev.length) return prev;
      const next = [...prev];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  // A preset keeps the lock on keys the type already has, so loading one over
  // an existing vocabulary cannot rename a stage out from under its cards.
  const loadPreset = (preset: LifecycleStageDef[]) => {
    const existing = new Set((customStagesOf(cardType) ?? []).map((s) => s.key));
    setRows(preset.map((s) => ({ ...s, _original: existing.has(s.key) })));
    setInUse(null);
  };

  const handleSave = async () => {
    if (!cardType) return;
    setSaving(true);
    setError(null);
    const stages = rows.map(({ _original, ...r }) => ({
      ...r,
      label: (r.translations?.[locale] ?? r.label).trim() || r.label,
      color: r.color || DEFAULT_OPTION_COLOR,
      translations: cleanTranslationMap(r.translations),
    }));
    try {
      await api.patch(`/metamodel/types/${cardType.key}`, {
        lifecycle_config: { stages },
        ...(inUse ? { lifecycle_reassign: reassign } : {}),
      });
      onSaved();
      onClose();
    } catch (e) {
      if (e instanceof ApiError && isInUse(e.detail)) {
        setInUse(e.detail);
        setReassign((prev) => {
          const next: Record<string, string> = {};
          for (const key of Object.keys((e.detail as InUseDetail).in_use)) next[key] = prev[key] ?? "";
          return next;
        });
      } else {
        setError(messageOf(e, t("metamodel.lifecycleStages.saveFailed")));
      }
    } finally {
      setSaving(false);
    }
  };

  const labelOf = (r: Row) => r.translations?.[locale] ?? r.label ?? "";
  const invalid = rows.some((r) => !isValidKey(r.key) || !labelOf(r).trim());
  const counts = new Map<string, number>();
  for (const r of rows) if (r.key) counts.set(r.key, (counts.get(r.key) || 0) + 1);
  const duplicateKeys = new Set([...counts].filter(([, n]) => n > 1).map(([k]) => k));
  const reassignIncomplete = !!inUse && Object.values(reassign).some((target) => !target);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth disableRestoreFocus>
      <DialogTitle>{t("metamodel.lifecycleStages.title")}</DialogTitle>
      <DialogContent>
        {error && (
          <Alert severity="error" sx={{ mb: 2, mt: 1 }}>
            {error}
          </Alert>
        )}
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2, mt: 1 }}>
          {t("metamodel.lifecycleStages.help")}
        </Typography>

        <Box sx={{ display: "flex", gap: 1, mb: 2, flexWrap: "wrap" }}>
          <Button size="small" variant="outlined" onClick={() => loadPreset(RADAR_PRESET)}>
            {t("metamodel.lifecycleStages.presetRadar")}
          </Button>
          <Button size="small" variant="outlined" onClick={() => loadPreset(DEFAULT_STAGES)}>
            {t("metamodel.lifecycleStages.presetDefault")}
          </Button>
          <Button size="small" onClick={() => loadPreset([])}>
            {t("metamodel.lifecycleStages.useBuiltIn")}
          </Button>
        </Box>

        {rows.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic", mb: 2 }}>
            {t("metamodel.lifecycleStages.builtIn")}
          </Typography>
        )}

        {rows.map((row, index) => (
          <Box key={index} sx={{ display: "flex", gap: 1, mb: 0.75, alignItems: "flex-start" }}>
            <Box sx={{ display: "flex", flexDirection: "column" }}>
              <IconButton
                size="small"
                sx={{ p: 0 }}
                disabled={index === 0}
                onClick={() => move(index, -1)}
                aria-label={t("metamodel.lifecycleStages.moveUp")}
              >
                <MaterialSymbol icon="keyboard_arrow_up" size={18} />
              </IconButton>
              <IconButton
                size="small"
                sx={{ p: 0 }}
                disabled={index === rows.length - 1}
                onClick={() => move(index, 1)}
                aria-label={t("metamodel.lifecycleStages.moveDown")}
              >
                <MaterialSymbol icon="keyboard_arrow_down" size={18} />
              </IconButton>
            </Box>
            <KeyInput
              size="small"
              label={t("metamodel.fieldEditor.optionKeyLabel")}
              value={row.key}
              onChange={(v) => patchRow(index, { key: v })}
              locked={!!row._original}
              lockedReason={t("metamodel.lifecycleStages.keyLocked")}
              sx={{ flex: 1 }}
              required
              externalError={duplicateKeys.has(row.key) ? t("validation:key.duplicate") : undefined}
            />
            <TextField
              size="small"
              label={`${t("metamodel.fieldEditor.optionLabelLabel")} (${
                LOCALE_LABELS[locale as keyof typeof LOCALE_LABELS] || locale
              })`}
              value={labelOf(row)}
              onChange={(e) => setLabelText(index, e.target.value)}
              sx={{ flex: 1 }}
              error={!labelOf(row).trim()}
            />
            <TextField
              select
              size="small"
              label={t("metamodel.lifecycleStages.semantic")}
              value={row.semantic}
              onChange={(e) =>
                patchRow(index, { semantic: e.target.value as LifecycleStageDef["semantic"] })
              }
              sx={{ width: 190 }}
            >
              {STAGE_SEMANTICS.map((s) => (
                <MenuItem key={s} value={s}>
                  {t(`metamodel.lifecycleStages.semantics.${s}`)}
                </MenuItem>
              ))}
            </TextField>
            <ColorPicker
              compact
              value={row.color || DEFAULT_OPTION_COLOR}
              onChange={(c) => patchRow(index, { color: c })}
            />
            <IconButton
              size="small"
              onClick={() => setRows((prev) => prev.filter((_, i) => i !== index))}
              aria-label={t("common:actions.delete")}
            >
              <MaterialSymbol icon="close" size={18} />
            </IconButton>
          </Box>
        ))}

        <Button
          size="small"
          startIcon={<MaterialSymbol icon="add" size={16} />}
          onClick={() =>
            setRows((prev) => [
              ...prev,
              { key: "", label: "", color: DEFAULT_OPTION_COLOR, semantic: "operational" },
            ])
          }
        >
          {t("metamodel.lifecycleStages.add")}
        </Button>

        {inUse && (
          <Alert severity="warning" sx={{ mt: 2 }}>
            <Typography variant="body2" sx={{ mb: 1.5 }}>
              {t("metamodel.lifecycleStages.inUse")}
            </Typography>
            {Object.entries(inUse.in_use).map(([key, count]) => (
              <Box key={key} sx={{ display: "flex", alignItems: "center", gap: 1.5, mb: 1 }}>
                <Typography variant="body2" sx={{ minWidth: 200 }}>
                  {t("metamodel.lifecycleStages.inUseRow", { stage: key, count })}
                </Typography>
                <TextField
                  select
                  size="small"
                  label={t("metamodel.lifecycleStages.moveTo")}
                  value={reassign[key] ?? ""}
                  onChange={(e) => setReassign((prev) => ({ ...prev, [key]: e.target.value }))}
                  sx={{ minWidth: 200 }}
                >
                  {inUse.valid_stages.map((target) => (
                    <MenuItem key={target} value={target}>
                      {target}
                    </MenuItem>
                  ))}
                </TextField>
              </Box>
            ))}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("common:actions.cancel")}</Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={saving || invalid || duplicateKeys.size > 0 || reassignIncomplete}
        >
          {inUse ? t("metamodel.lifecycleStages.reassignAndSave") : t("common:actions.save")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
