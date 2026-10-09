"""Configurable lifecycle stages, per card type.

Two additive columns, no backfill.

``card_types.lifecycle_config`` — the type's ordered stage vocabulary:
``{"stages": [{key, label, color, semantic, translations}]}``. Empty means the
built-in five-phase model (plan / phaseIn / active / phaseOut / endOfLife).

``cards.lifecycle_stage`` — the explicit current stage, recorded without a
date. ``NULL`` means "not stated", in which case the current stage is derived
from the dates in ``cards.lifecycle`` exactly as before.

Deliberately no backfill: nothing is written to ``lifecycle_stage`` from the
existing dates and no existing ``lifecycle`` key is renamed or reinterpreted,
so every install is unchanged until an admin configures stages on a type.

Downgrade drops both columns, which discards the vocabularies and every
explicitly recorded stage. Lifecycle dates are untouched either way.

Revision ID: 154
Revises: 153
Create Date: 2026-10-09
"""

from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "154"
down_revision: Union[str, None] = "153"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _columns(table: str) -> set[str]:
    from sqlalchemy import inspect as sa_inspect

    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "lifecycle_stage" not in _columns("cards"):
        op.add_column("cards", sa.Column("lifecycle_stage", sa.String(length=50), nullable=True))
    if "lifecycle_config" not in _columns("card_types"):
        op.add_column(
            "card_types",
            sa.Column(
                "lifecycle_config",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default="{}",
            ),
        )


def downgrade() -> None:
    if "lifecycle_config" in _columns("card_types"):
        op.drop_column("card_types", "lifecycle_config")
    if "lifecycle_stage" in _columns("cards"):
        op.drop_column("cards", "lifecycle_stage")
