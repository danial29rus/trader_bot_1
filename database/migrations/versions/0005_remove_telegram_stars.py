"""remove Telegram Stars from the public catalogue"""
from alembic import op


revision = "0005_remove_telegram_stars"
down_revision = "0004_catalog_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep referenced products and categories for existing carts/orders, but make
    # them unavailable in the storefront and prevent new purchases.
    op.execute("UPDATE products SET active = false WHERE product_type = 'telegram' AND external_id LIKE 'stars:%'")
    op.execute("UPDATE categories SET active = false WHERE slug = 'telegram-stars'")


def downgrade() -> None:
    op.execute("UPDATE categories SET active = true WHERE slug = 'telegram-stars'")
    op.execute("UPDATE products SET active = true WHERE product_type = 'telegram' AND external_id LIKE 'stars:%'")
