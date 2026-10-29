"""Allow the local development storage scheme.

The dataset registry stores an object URI. Production uses ``s3://``; the
development backend writes plain files and uses ``local://``. The original
constraint only accepted ``s3://%``, so the very first Bronze write of a local
run was rejected by Postgres.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("storage_uri_scheme", "datasets", type_="check")
    op.create_check_constraint(
        "storage_uri_scheme",
        "datasets",
        "storage_uri like 's3://%' or storage_uri like 'local://%'",
    )


def downgrade() -> None:
    op.drop_constraint("storage_uri_scheme", "datasets", type_="check")
    op.create_check_constraint(
        "storage_uri_scheme", "datasets", "storage_uri like 's3://%'"
    )
