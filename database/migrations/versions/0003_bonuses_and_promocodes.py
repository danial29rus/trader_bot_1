"""add bonuses and promo codes"""
from alembic import op
import sqlalchemy as sa

revision = "0003_bonuses_and_promocodes"
down_revision = "0002_support_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("bonus_balance_minor", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("subtotal_minor", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("promo_code", sa.String(40)))
    op.add_column("orders", sa.Column("promo_discount_minor", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("bonus_used_minor", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("bonus_accrued_minor", sa.BigInteger(), nullable=False, server_default="0"))
    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("usage_limit", sa.Integer()),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_promo_codes_code", "promo_codes", ["code"])


def downgrade() -> None:
    op.drop_index("ix_promo_codes_code", table_name="promo_codes")
    op.drop_table("promo_codes")
    for column in ("bonus_accrued_minor", "bonus_used_minor", "promo_discount_minor", "promo_code", "subtotal_minor"):
        op.drop_column("orders", column)
    op.drop_column("users", "bonus_balance_minor")
