"""Configurable lifecycle stages, per card type.

A card type's ``lifecycle_config`` holds an ordered stage vocabulary::

    {"stages": [{"key", "label", "color", "semantic", "translations"}, ...]}

An empty config means the built-in five-phase model (:data:`DEFAULT_STAGES`),
which is why every install that never configures stages behaves exactly as it
did before the column existed.

Two facts are recorded per card and kept apart on purpose:

* ``cards.lifecycle`` — stage key → ISO date. Dated history and planned
  transitions; a date in the future is a plan, not the current state.
* ``cards.lifecycle_stage`` — the explicit current stage, recorded without any
  date. ``NULL`` means "not stated": the current stage is then derived from the
  dates, and a card with neither is *unknown*, never assumed operational.

Reports must not test stage keys. They ask for a stage's **semantic**
(:data:`SEMANTICS`), which is the one thing a renamed or re-ordered vocabulary
keeps stable.

``cards.status`` (ACTIVE / ARCHIVED) is not a lifecycle stage. It is record
retention, moved only by the archive and restore actions; nothing here reads or
writes it, so the two can never be edited into contradiction.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.card import Card

PRE_OPERATIONAL = "pre_operational"
OPERATIONAL = "operational"
RETIRING = "retiring"
RETIRED = "retired"

#: What a stage means to a report, in lifecycle order.
SEMANTICS: tuple[str, ...] = (PRE_OPERATIONAL, OPERATIONAL, RETIRING, RETIRED)

#: The built-in five-phase model. Keys and order are the ones the application
#: has always stored, so existing lifecycle dates need no migration.
DEFAULT_STAGES: tuple[dict, ...] = (
    {"key": "plan", "label": "Plan", "color": "#9e9e9e", "semantic": PRE_OPERATIONAL},
    {"key": "phaseIn", "label": "Phase In", "color": "#1976d2", "semantic": PRE_OPERATIONAL},
    {"key": "active", "label": "Active", "color": "#2e7d32", "semantic": OPERATIONAL},
    {"key": "phaseOut", "label": "Phase Out", "color": "#ed6c02", "semantic": RETIRING},
    {"key": "endOfLife", "label": "End of Life", "color": "#c62828", "semantic": RETIRED},
)

MAX_STAGES = 12

_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,49}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class LifecycleConfigError(ValueError):
    """A ``lifecycle_config`` payload that cannot be stored."""


def validate_lifecycle_config(raw: object) -> dict:
    """Normalise a ``lifecycle_config`` payload, or raise.

    ``None``, ``{}`` and an empty stage list all mean "use the built-in model"
    and normalise to ``{}``.
    """
    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict):
        raise LifecycleConfigError("lifecycle_config must be an object")
    stages = raw.get("stages")
    if stages is None or stages == []:
        return {}
    if not isinstance(stages, list):
        raise LifecycleConfigError("lifecycle_config.stages must be a list")
    if len(stages) > MAX_STAGES:
        raise LifecycleConfigError(f"A card type can have at most {MAX_STAGES} lifecycle stages")
    seen: set[str] = set()
    out: list[dict] = []
    for stage in stages:
        if not isinstance(stage, dict):
            raise LifecycleConfigError("Each lifecycle stage must be an object")
        key = stage.get("key")
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise LifecycleConfigError(
                f"Invalid lifecycle stage key {key!r}: use letters, digits and "
                "underscores, starting with a letter (50 characters at most)"
            )
        if key in seen:
            raise LifecycleConfigError(f"Duplicate lifecycle stage key {key!r}")
        seen.add(key)
        label = stage.get("label")
        if not isinstance(label, str) or not label.strip():
            raise LifecycleConfigError(f"Lifecycle stage {key!r} needs a label")
        color = stage.get("color")
        if not isinstance(color, str) or not _COLOR_RE.match(color):
            raise LifecycleConfigError(
                f"Lifecycle stage {key!r} needs a colour in #rrggbb form, got {color!r}"
            )
        semantic = stage.get("semantic")
        if semantic not in SEMANTICS:
            raise LifecycleConfigError(
                f"Lifecycle stage {key!r} needs a semantic, one of: {', '.join(SEMANTICS)}"
            )
        translations = stage.get("translations") or {}
        if not isinstance(translations, dict):
            raise LifecycleConfigError(f"Lifecycle stage {key!r}: translations must be an object")
        out.append(
            {
                "key": key,
                "label": label.strip(),
                "color": color.lower(),
                "semantic": semantic,
                "translations": translations,
            }
        )
    return {"stages": out}


def stages_for(config: dict | None) -> list[dict]:
    """The ordered stages a card type uses: its own, or the built-in model."""
    stages = (config or {}).get("stages")
    if stages:
        return list(stages)
    return [dict(s) for s in DEFAULT_STAGES]


def is_custom(config: dict | None) -> bool:
    return bool((config or {}).get("stages"))


def stage_keys(stages: list[dict]) -> list[str]:
    return [s["key"] for s in stages]


def semantic_of(stages: list[dict], key: str | None) -> str | None:
    """The semantic of a stage key, ``None`` for an unknown or absent key."""
    for s in stages:
        if s["key"] == key:
            return str(s["semantic"])
    return None


def keys_with_semantic(stages: list[dict], *semantics: str) -> list[str]:
    """Stage keys carrying any of ``semantics``, in vocabulary order."""
    return [s["key"] for s in stages if s["semantic"] in semantics]


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        text_value = str(value)
        if "T" in text_value:
            return datetime.fromisoformat(text_value).date()
        return datetime.strptime(text_value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def dated_stage(
    lifecycle: dict | None, stages: list[dict], today: date | None = None
) -> str | None:
    """The stage the dates alone put a card in.

    The most advanced stage whose date has passed; when every date is still
    ahead, the earliest dated stage. ``None`` when no stage is dated.
    """
    if not lifecycle:
        return None
    today = today or datetime.now(timezone.utc).date()
    keys = stage_keys(stages)
    for key in reversed(keys):
        d = _parse_date(lifecycle.get(key))
        if d is not None and d <= today:
            return key
    for key in keys:
        if lifecycle.get(key):
            return key
    return None


def current_stage(
    lifecycle: dict | None,
    explicit: str | None,
    stages: list[dict],
    today: date | None = None,
) -> str | None:
    """A card's current stage: the explicit one when stated, else by date.

    ``None`` is *unknown* — no explicit stage and no dated stage. Callers must
    not read that as operational.
    """
    if explicit:
        return explicit
    return dated_stage(lifecycle, stages, today)


def removed_stage_keys(old_config: dict | None, new_config: dict | None) -> set[str]:
    """Stage keys the old vocabulary has and the new one drops."""
    return set(stage_keys(stages_for(old_config))) - set(stage_keys(stages_for(new_config)))


def _dated(key: str):
    """SQL: the card carries a non-empty date under lifecycle ``key``."""
    return func.coalesce(Card.lifecycle[key].astext, "") != ""


async def stage_usage(db: AsyncSession, type_key: str, keys: set[str]) -> dict[str, int]:
    """How many cards of a type use each of ``keys``, explicitly or by date.

    Archived cards count: they can be restored, and would come back holding a
    stage their type no longer defines.
    """
    usage: dict[str, int] = {}
    for key in sorted(keys):
        count = (
            await db.execute(
                select(func.count(Card.id)).where(
                    Card.type == type_key,
                    (Card.lifecycle_stage == key) | _dated(key),
                )
            )
        ).scalar_one()
        if count:
            usage[key] = count
    return usage


async def reassignment_conflicts(db: AsyncSession, type_key: str, old: str, new: str) -> int:
    """Cards dated under both ``old`` and ``new``.

    Moving ``old``'s date onto ``new`` would have to discard one of the two, so
    such cards block a reassignment instead of losing a date silently.
    """
    return (
        await db.execute(
            select(func.count(Card.id)).where(Card.type == type_key, _dated(old), _dated(new))
        )
    ).scalar_one()


async def reassign_stage(db: AsyncSession, type_key: str, old: str, new: str) -> None:
    """Move every use of stage ``old`` on a type's cards to ``new``.

    Rewrites the explicit stage and renames the dated key, keeping the date.
    The caller has already established there are no conflicts.
    """
    await db.execute(
        text(
            "UPDATE cards SET lifecycle_stage = :new WHERE type = :type AND lifecycle_stage = :old"
        ),
        {"type": type_key, "old": old, "new": new},
    )
    await db.execute(
        text(
            "UPDATE cards "
            "SET lifecycle = (lifecycle - CAST(:old AS text)) "
            "    || jsonb_build_object(CAST(:new AS text), lifecycle -> CAST(:old AS text)) "
            "WHERE type = :type AND COALESCE(lifecycle ->> CAST(:old AS text), '') <> ''"
        ),
        {"type": type_key, "old": old, "new": new},
    )
    # A key left holding an empty value carries no date; drop it so the stage
    # stops reading as present on the card.
    await db.execute(
        text(
            "UPDATE cards SET lifecycle = lifecycle - CAST(:old AS text) "
            "WHERE type = :type AND lifecycle ? CAST(:old AS text)"
        ),
        {"type": type_key, "old": old},
    )


# ---------------------------------------------------------------------------
# Reporting view
# ---------------------------------------------------------------------------
# Reports are written against the built-in phase names: "live from `active`",
# "retired at `endOfLife`". Rather than teach every one of them about every
# vocabulary, a card on a custom vocabulary is *projected* onto those four
# semantic slots before a report sees it. A card on the built-in model with no
# explicit stage passes through untouched, so those reports are unchanged.

#: The built-in phase that carries each semantic in a report.
SEMANTIC_SLOT: dict[str, str] = {
    PRE_OPERATIONAL: "plan",
    OPERATIONAL: "active",
    RETIRING: "phaseOut",
    RETIRED: "endOfLife",
}

#: Reserved, non-date member of a projected lifecycle. Set only when the card's
#: explicit stage says something no date does — retired with no retirement
#: date, or pre-operational with no start date — so a report can still treat
#: the card as retired / not started without a date being invented for it.
SEMANTIC_KEY = "_semantic"


def report_lifecycle(
    lifecycle: dict | None, explicit: str | None, config: dict | None
) -> dict | None:
    """A card's lifecycle as reports read it: dates under the built-in phase
    names, by semantic. The earliest date wins when several stages share one.
    """
    custom = is_custom(config)
    if not custom and not explicit:
        return lifecycle
    stages = stages_for(config)
    source = lifecycle or {}
    if custom:
        out: dict = {}
        for stage in stages:
            value = source.get(stage["key"])
            if not value:
                continue
            slot = SEMANTIC_SLOT[stage["semantic"]]
            if slot not in out or str(value) < str(out[slot]):
                out[slot] = value
    else:
        out = dict(source)
    semantic = semantic_of(stages, explicit)
    if semantic == RETIRED and not out.get("endOfLife"):
        out[SEMANTIC_KEY] = RETIRED
    elif semantic == PRE_OPERATIONAL and not any(out.get(k) for k in ("plan", "phaseIn", "active")):
        out[SEMANTIC_KEY] = PRE_OPERATIONAL
    return out


def report_phase(
    lifecycle: dict | None, explicit: str | None, config: dict | None, today: date | None = None
) -> str | None:
    """The built-in phase name a card's current stage counts under in a
    cross-type distribution, or ``None`` when its stage is unknown."""
    stages = stages_for(config)
    key = current_stage(lifecycle, explicit, stages, today)
    if key is None:
        return None
    if not is_custom(config):
        return key
    semantic = semantic_of(stages, key)
    return SEMANTIC_SLOT[semantic] if semantic else None


def has_lifecycle_data(lifecycle: dict | None, explicit: str | None) -> bool:
    """Whether anything is recorded about a card's lifecycle: a stage or a date."""
    return bool(explicit) or any((lifecycle or {}).values())


async def load_configs(db: AsyncSession) -> dict[str, dict]:
    """Every card type's custom vocabulary, keyed by type. Types on the
    built-in model are absent."""
    from app.models.card_type import CardType

    rows = await db.execute(select(CardType.key, CardType.lifecycle_config))
    return {key: config for key, config in rows.all() if is_custom(config)}


def retired_keys(configs: dict[str, dict], type_key: str) -> list[str]:
    """The lifecycle keys under which a type records a retirement date."""
    return keys_with_semantic(stages_for(configs.get(type_key)), RETIRED)


class LifecycleView:
    """Per-request projector: ``view(card)`` is the card's reporting lifecycle."""

    def __init__(self, configs: dict[str, dict]):
        self.configs = configs

    def __call__(self, card) -> dict | None:
        return report_lifecycle(
            card.lifecycle, getattr(card, "lifecycle_stage", None), self.configs.get(card.type)
        )

    def phase(self, card) -> str | None:
        return report_phase(
            card.lifecycle, getattr(card, "lifecycle_stage", None), self.configs.get(card.type)
        )

    def proxy(self, card):
        """A stand-in exposing ``id`` / ``type`` / ``attributes`` and the
        projected ``lifecycle``, for helpers that take card-shaped objects."""
        from types import SimpleNamespace

        return SimpleNamespace(
            id=card.id, type=card.type, attributes=card.attributes, lifecycle=self(card)
        )


async def lifecycle_view(db: AsyncSession) -> LifecycleView:
    return LifecycleView(await load_configs(db))
