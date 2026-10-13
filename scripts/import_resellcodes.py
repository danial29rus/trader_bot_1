import asyncio
import logging

from config import get_settings
from database.session import SessionLocal
from services.import_service import import_catalog


async def main() -> None:
    if not get_settings().resellcodes_api_key:
        raise SystemExit("RESELLCODES_API_KEY is empty; bot can work with the demo catalogue.")
    async with SessionLocal() as session:
        result = await import_catalog(session)
    print(f"Imported: {result}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
