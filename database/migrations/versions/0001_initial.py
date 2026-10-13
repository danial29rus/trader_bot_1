"""initial storefront schema"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("users", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True), sa.Column("username", sa.String(64)), sa.Column("first_name", sa.String(128)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"])
    op.create_table("categories", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(255), nullable=False), sa.Column("slug", sa.String(160), nullable=False, unique=True), sa.Column("parent_id", sa.Integer(), sa.ForeignKey("categories.id")), sa.Column("type", sa.String(40), nullable=False), sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table("products", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id")), sa.Column("supplier", sa.String(40), nullable=False), sa.Column("external_id", sa.String(255), nullable=False), sa.Column("product_type", sa.String(40), nullable=False), sa.Column("name", sa.String(500), nullable=False), sa.Column("description", sa.Text()), sa.Column("image_url", sa.Text()), sa.Column("platform", sa.String(100)), sa.Column("region", sa.String(100)), sa.Column("activation_type", sa.String(100)), sa.Column("price_minor", sa.BigInteger(), nullable=False), sa.Column("currency", sa.String(3), nullable=False), sa.Column("available_by_import", sa.Boolean()), sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("imported_at", sa.DateTime(timezone=True)), sa.Column("raw_payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")), sa.UniqueConstraint("supplier", "product_type", "external_id", name="uq_product_external"))
    op.create_table("cart_items", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False), sa.Column("quantity", sa.Integer(), nullable=False), sa.Column("configuration", postgresql.JSONB()), sa.Column("configuration_key", sa.String(64), nullable=False), sa.UniqueConstraint("user_id", "product_id", "configuration_key", name="uq_cart_line"))
    op.create_index("ix_cart_items_user_id", "cart_items", ["user_id"])
    op.create_table("orders", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("public_number", sa.String(40), nullable=False, unique=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("payment_method", sa.String(30), nullable=False), sa.Column("total_minor", sa.BigInteger(), nullable=False), sa.Column("currency", sa.String(3), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("paid_at", sa.DateTime(timezone=True)))
    op.create_index("ix_orders_user_id", "orders", ["user_id"])
    op.create_index("ix_orders_public_number", "orders", ["public_number"])
    op.create_table("order_items", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False), sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id")), sa.Column("product_name_snapshot", sa.String(500), nullable=False), sa.Column("configuration_snapshot", postgresql.JSONB()), sa.Column("unit_price_minor", sa.BigInteger(), nullable=False), sa.Column("quantity", sa.Integer(), nullable=False), sa.Column("total_minor", sa.BigInteger(), nullable=False))
    op.create_table("payments", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True), sa.Column("method", sa.String(30), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("amount_minor", sa.BigInteger(), nullable=False), sa.Column("is_test", sa.Boolean(), nullable=False), sa.Column("reference", sa.String(80), nullable=False, unique=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    op.create_table("import_runs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("status", sa.String(30), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("counters", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("error_summary", sa.Text()))
    op.create_table("audit_log", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("actor_telegram_id", sa.BigInteger(), nullable=False), sa.Column("action", sa.String(100), nullable=False), sa.Column("entity", sa.String(100), nullable=False), sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))


def downgrade() -> None:
    for table in ("audit_log", "import_runs", "payments", "order_items", "orders", "cart_items", "products", "categories", "users"):
        op.drop_table(table)
