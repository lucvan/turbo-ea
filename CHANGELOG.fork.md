# Fork changelog

Changes in `lucvan/turbo-ea` that are not in upstream `vincentmakes/turbo-ea`.
Upstream's own history stays in `CHANGELOG.md`, and `VERSION` follows upstream,
so syncing the fork never conflicts on either file.

## 2026-10-09 — Configurable lifecycle stages

Database migration 154 (two added columns, no backfill).

### Added
- Each card type can define its own ordered lifecycle stages, with a stable key, a label, a colour, translations and a meaning (pre-operational, operational, retiring, retired). A type that defines none keeps the five built-in phases and behaves as before. A ready-made Evaluating → Emerging → Core → Heritage → Sunset → Discontinued vocabulary can be loaded in the editor.
- A card can record its current lifecycle stage without a date. Lifecycle dates stay as the dated history and planned transitions. A card with neither a stated stage nor a date is unknown, never assumed active.
- The lifecycle badge, the card's lifecycle section, the inventory filter, column and grouping, the Excel import and export, the CSV and JSON exports, workspace transfer, the REST API and the MCP server (`set_card_lifecycle_stage`) all carry the stated stage and a type's own stages.
- Reports, cost figures, the dashboard and end-of-life coverage read a type's own stages by their meaning.

### Changed
- A lifecycle stage that cards still use cannot be removed from a type unless the cards are reassigned to a remaining stage in the same step; a reassignment that would overwrite an existing date is refused.
- A card's `status` accepts only `ACTIVE` and `ARCHIVED`. It records retention, not lifecycle, so lifecycle-like values (`PHASING_OUT`, `END_OF_LIFE`) are refused by the API and are no longer offered by the MCP `transition_card_lifecycle` tool.
- Changing a card's stated lifecycle stage breaks an approval, like changing its lifecycle dates.

### Known limits
- A card whose stage is stated without a date has no row on timeline reports.
- In reports that span card types, a type's own stages are shown under the built-in phase with the same meaning, not under their own names.
- The user guide for this feature is written in English only.
