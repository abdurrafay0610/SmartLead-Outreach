"""add num_emails_per_lead to internal_campaigns

Revision ID: add_num_emails_per_lead
Revises: <REPLACE_WITH_CURRENT_HEAD>
Create Date: 2026-04-01
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_num_emails_per_lead"
down_revision = "537a85a77311"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "internal_campaigns",
        sa.Column(
            "num_emails_per_lead",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("internal_campaigns", "num_emails_per_lead")