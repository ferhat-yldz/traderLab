import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from traderlab.infrastructure.database.session import create_db_engine, get_session_factory
from sqlalchemy import text

async def check_db():
    engine = create_db_engine()
    factory = get_session_factory(engine)
    async with factory() as session:
        res = await session.execute(text("SELECT current_database(), current_user, version();"))
        db, user, ver = res.fetchone()
        print(f"Connected to DB: {db} as {user}")
        print(f"PostgreSQL version: {ver[:60]}...")
        
        tables_res = await session.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';"
        ))
        tables = [r[0] for r in tables_res.fetchall()]
        print("Tables in public schema:", tables)
        
        if "candles" in tables:
            cnt_res = await session.execute(text("SELECT COUNT(*) FROM candles;"))
            cnt = cnt_res.scalar()
            print(f"Current row count in candles table: {cnt:,}")
            
            cnt_by_type = await session.execute(text(
                "SELECT price_type, COUNT(*) FROM candles GROUP BY price_type;"
            ))
            for pt, c in cnt_by_type.fetchall():
                print(f"  - price_type={pt}: {c:,} rows")

if __name__ == "__main__":
    asyncio.run(check_db())
