"""Destructively clear storefront data while keeping the Alembic schema in place."""
import argparse
import asyncio

from sqlalchemy import delete

from database.models import AuditLog, CartItem, Category, ImportRun, Order, OrderItem, Payment, Product, SupportMessage, SupportTicket, User
from database.session import SessionLocal


async def main() -> None:
    parser = argparse.ArgumentParser(description="Delete all storefront data; migrations are preserved.")
    parser.add_argument("--yes", action="store_true", help="required acknowledgement")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing destructive reset. Run with --yes after making a backup.")
    async with SessionLocal() as session, session.begin():
        for model in (AuditLog, SupportMessage, SupportTicket, Payment, OrderItem, CartItem, Product, Order, ImportRun, Category, User):
            await session.execute(delete(model))
    print("Storefront data deleted; database schema and migrations were kept.")


if __name__ == "__main__":
    asyncio.run(main())
