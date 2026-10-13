"""catalog snapshot: category images/order, supplier price and manual price lock"""
from alembic import op
import sqlalchemy as sa

revision = "0004_catalog_snapshot"
down_revision = "0003_bonuses_and_promocodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("categories", sa.Column("image_url", sa.Text()))
    op.add_column("categories", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="1000"))
    op.add_column("products", sa.Column("supplier_price_usd", sa.Numeric(12, 4)))
    op.add_column("products", sa.Column("price_locked", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("products", "price_locked")
    op.drop_column("products", "supplier_price_usd")
    op.drop_column("categories", "sort_order")
    op.drop_column("categories", "image_url")
