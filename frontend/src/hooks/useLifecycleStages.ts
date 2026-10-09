import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useMetamodel } from "@/hooks/useMetamodel";
import { resolveLabel } from "@/hooks/useResolveLabel";
import { getPhaseLabels } from "@/lib/lifecyclePhases";
import { customStagesOf, currentStage, findStage, stagesOf } from "@/lib/lifecycleStages";
import type { LifecycleStageDef } from "@/types";

type CardLike = {
  type: string;
  lifecycle?: Record<string, string> | null;
  lifecycle_stage?: string | null;
};

/**
 * Lifecycle stage lookups by card type, off the cached metamodel.
 *
 * The built-in phases are labelled through i18n (they are translated in every
 * locale); a type's own stages are labelled from their stored translations.
 */
export function useLifecycleStages() {
  const { types } = useMetamodel();
  const { t, i18n } = useTranslation("common");
  const byKey = useMemo(() => new Map(types.map((ct) => [ct.key, ct])), [types]);
  const phaseLabels = useMemo(() => getPhaseLabels(t), [t]);

  /** The type's own vocabulary, or `undefined` for the built-in phases. */
  const customStages = useCallback(
    (typeKey: string | undefined): LifecycleStageDef[] | undefined =>
      typeKey ? customStagesOf(byKey.get(typeKey)) : undefined,
    [byKey],
  );

  /** The ordered stages in force for a type. */
  const stages = useCallback(
    (typeKey: string | undefined): LifecycleStageDef[] =>
      stagesOf(typeKey ? byKey.get(typeKey) : undefined),
    [byKey],
  );

  /** A card's current stage key, or "" when it is unknown. */
  const stageOf = useCallback(
    (card: CardLike): string => currentStage(card, stages(card.type)) ?? "",
    [stages],
  );

  /** Display label of a stage key on a type; the key itself when undefined. */
  const stageLabel = useCallback(
    (typeKey: string | undefined, key: string): string => {
      const custom = customStages(typeKey);
      if (!custom) return phaseLabels[key] ?? key;
      const def = findStage(custom, key);
      return def ? resolveLabel(def.label, def.translations, i18n.language) : key;
    },
    [customStages, phaseLabels, i18n.language],
  );

  return { customStages, stages, stageOf, stageLabel };
}
