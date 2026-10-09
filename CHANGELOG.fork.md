# Fork changelog

Changes in `lucvan/turbo-ea` that are not in upstream `vincentmakes/turbo-ea`.
Upstream's own history stays in `CHANGELOG.md`.

## How a fork release is numbered and announced

The app announces a new version to every user once it is running, and shows
the changelog for the versions they skipped (bell notification, "what's new"
dialog). Fork changes go through the same path:

- `VERSION` follows upstream and is never edited here, so a sync does not
  conflict on it. `FORK_VERSION` holds this fork's own revision, a whole number
  that only goes up. The running version is `<VERSION>.<FORK_VERSION>`, for
  example `2.158.0.2`.
- Every change that reaches `main`, **including a sync with upstream**, raises
  `FORK_VERSION` by one and adds a `## [<VERSION>.<FORK_VERSION>] - <date>`
  section below, newest first. A test fails if the running version has none.
  For a sync, the section says which upstream version was merged; upstream's
  own entries are shown from `CHANGELOG.md` and need not be repeated.
- In the dialog every section heading names its source: this fork, or upstream
  Turbo EA.

## [2.158.0.2] - 2026-10-09

No database migration.

### Added
- Changes made in this fork are announced like any other update: the version shown in the app carries the fork's revision (`2.158.0.2`), the "updated" notification fires for a fork release, and the "what's new" dialog lists fork and upstream changes together, each headed with its source.

### Fixed
- MCP server: a dry run of more than 20 rows failed with "Missing or invalid confirm_token" instead of returning its preview and token. This affected `update_cards_bulk`, `create_cards_bulk` and `archive_cards`; nothing was written, but a larger change could not be previewed at all.
- MCP server: `update_cards_bulk` reports every row at its own position. A call mixing different patches used to restart `row_index` for each patch, return the rows in a different order, and on commit return only the cards of the last patch.
- MCP server: `update_cards_bulk` is all-or-nothing as documented. Rows with different patches used to be saved one patch at a time, so a rejected row left the earlier patches saved.
- MCP server: `updated` counts the cards that changed. It used to repeat the number of rows sent, and a row that was already up to date, named an unknown card or misspelt a field was reported as updated.

### Security
- MCP server: the confirmation token of a large commit is now checked, and checked before anything is written. Any non-empty text used to be accepted. A token is valid for the user, tool, row count and exact rows of its dry run, for 15 minutes, once.
- MCP server: `set_card_logos` and `clear_card_logos` return a confirmation token from a dry run above the threshold; before, their commits asked for a token no call could supply.

### Changed
- MCP `update_cards_bulk` response: `results` holds one entry per input row in input order (`would_update`, `updated`, `unchanged`, `error` or `not_applied`), with the counts `total`, `would_update`, `updated`, `unchanged`, `failed` and `not_applied` and a `committed` flag. The `result` key of a commit (the cards of the last patch) is gone. A card may appear once per call, and a field a card does not have is rejected instead of ignored.
- A dry run with a failed row issues no confirmation token. Dry runs return `confirm_token_expires_at` with the token.
- REST API: `PATCH /cards/bulk-rows` updates many cards with one patch each in a single transaction and returns a result per row. On `PATCH /cards/bulk` with `dry_run`, `row_index` is the card's position in `ids`.
- Audit log: a batch summary records `status` as `previewed`, `committed` or `failed`. If the audit batch cannot be closed, the tool result carries an `audit_warning` saying whether data changed.

### Known limits
- `upsert_relations_bulk` opens no audit batch and asks for no confirmation token, whatever its size.
- The updated MCP guide is in English only.

## [2.158.0.1] - 2026-10-09

Configurable lifecycle stages. Deployed as 2.158.0, before fork releases were numbered.

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
