"""Installation-wide LLM provider configuration and installation admins.

Revision ID: 0002
Revises: 0001

Why a new revision
------------------
``0001`` models tenancy: every table there is reachable from an organization
and therefore carries ``org_id`` plus an RLS policy. The LLM provider, however,
is configured **once per installation** (D-05: "LLM via adapter, configured by
env"), and installation admin is a deployment-wide flag that is deliberately
orthogonal to the organization role (viewer/editor/admin/owner). Neither table
has an ``org_id``, so neither can be added to ``ORG_SCOPED_TABLES`` without
inventing a column that does not exist.

Two tables
----------
``installation_settings``
    Singleton row (``id = 1`` enforced by a CHECK constraint). Holds the base
    URL, the models, the timeout, the prices and the **encrypted** API key.
    ``llm_api_key_encrypted`` is Fernet ciphertext produced with
    ``INSTALLATION_SECRET_KEY``; ``llm_api_key_hint`` keeps at most the last
    four characters so the UI can show "key is set" without a secret.

``install_admins``
    One row per user granted the installation admin flag. A separate table
    (rather than ``users.is_install_admin``) keeps "who granted it, when, why"
    next to the grant, which is what an operator needs when auditing.

No RLS here: there is no tenant to isolate. Access is gated in the application
by ``app.deps.require_install_admin``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. installation_settings - singleton row, deployment-wide.
    #
    #    ``id`` has no ``gen_random_uuid()`` default on purpose: the row is
    #    always created with the literal 1 so the CHECK constraint below makes
    #    a second row impossible at the database level, not just in the service.
    # ------------------------------------------------------------------
    op.create_table(
        "installation_settings",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("llm_base_url", sa.Text(), nullable=True),
        sa.Column("llm_api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("llm_api_key_hint", sa.String(8), nullable=True),
        sa.Column("llm_model_default", sa.String(128), nullable=True),
        sa.Column("llm_model_small", sa.String(128), nullable=True),
        sa.Column("llm_timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("llm_price_in_per_1k", sa.Numeric(12, 6), nullable=True),
        sa.Column("llm_price_out_per_1k", sa.Numeric(12, 6), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.String(254), nullable=True),
        sa.CheckConstraint("id = 1", name="installation_settings_singleton"),
        sa.CheckConstraint(
            "llm_timeout_seconds is null or "
            "(llm_timeout_seconds >= 1 and llm_timeout_seconds <= 300)",
            name="installation_settings_timeout_range",
        ),
        sa.CheckConstraint(
            "llm_price_in_per_1k is null or llm_price_in_per_1k >= 0",
            name="installation_settings_price_in_non_negative",
        ),
        sa.CheckConstraint(
            "llm_price_out_per_1k is null or llm_price_out_per_1k >= 0",
            name="installation_settings_price_out_non_negative",
        ),
    )

    # ------------------------------------------------------------------
    # 2. install_admins - deployment-wide flag, one row per user.
    #
    #    ON DELETE CASCADE: deleting the user removes the flag with it, so a
    #    recycled email can never inherit an old grant.
    # ------------------------------------------------------------------
    op.create_table(
        "install_admins",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("granted_by", sa.String(254), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    """Drop both tables. No ENUM type was created, so nothing else to undo."""
    op.drop_table("install_admins")
    op.drop_table("installation_settings")
