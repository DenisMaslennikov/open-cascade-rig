"""Initial revision: create the seen table that the wrapper's own model declares.

The migration is the schema's source of truth in production; the table is created here and
never through a declarative ``create_all`` call.
"""

from __future__ import annotations

revision: str = "0001"
down_revision: str | None = None
branch_labels: None = None
depends_on: None = None

__all__ = ["downgrade", "down_revision", "revision", "upgrade"]


def upgrade() -> None:
    """Create the seen table with its project/mark key and its epoch-seconds column."""
    raise NotImplementedError


def downgrade() -> None:
    """Drop the seen table created by this revision."""
    raise NotImplementedError
