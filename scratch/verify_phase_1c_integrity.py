import asyncio
import sys
from pathlib import Path
from decimal import Decimal
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from traderlab.infrastructure.database.session import create_db_engine
from traderlab.infrastructure.providers.market_data_lab.csv_reader import read_m1_csv
from traderlab.core.types import PriceType

async def run_sql_verifications():
    engine = create_db_engine()
    results = {}
    
    async with engine.connect() as conn:
        print("=== 1. POSTGRESQL VERİ BÜTÜNLÜĞÜ DOĞRULAMA SORGULARI ===")
        
        # 1. Total counts by price_type
        q1 = """
        SELECT price_type, COUNT(*) 
        FROM candles 
        WHERE instrument_id = 'XAUUSD' 
          AND timeframe = 60 
          AND source_id = 'market_data_lab_m1' 
          AND revision = 0
        GROUP BY price_type 
        ORDER BY price_type;
        """
        res1 = await conn.execute(text(q1))
        counts = dict(res1.fetchall())
        bid_count = counts.get("BID", 0)
        ask_count = counts.get("ASK", 0)
        total_count = bid_count + ask_count
        print(f"BID kayıt sayısı:         {bid_count:,}")
        print(f"ASK kayıt sayısı:         {ask_count:,}")
        print(f"Toplam kayıt sayısı:      {total_count:,}")
        results["bid_count"] = bid_count
        results["ask_count"] = ask_count
        results["total_count"] = total_count

        # 2. Sentetik kayıt sayısı
        q2 = """
        SELECT COUNT(*) 
        FROM candles 
        WHERE instrument_id = 'XAUUSD' 
          AND timeframe = 60 
          AND source_id = 'market_data_lab_m1' 
          AND revision = 0
          AND is_synthetic = TRUE;
        """
        res2 = await conn.execute(text(q2))
        synthetic_count = res2.scalar()
        print(f"Sentetik kayıt sayısı:    {synthetic_count}")
        results["synthetic_count"] = synthetic_count

        # 3. Mükerrer kayıt sayısı
        q3 = """
        SELECT open_time, price_type, COUNT(*) 
        FROM candles 
        WHERE instrument_id = 'XAUUSD' 
          AND timeframe = 60 
          AND source_id = 'market_data_lab_m1' 
          AND revision = 0
        GROUP BY open_time, price_type 
        HAVING COUNT(*) > 1;
        """
        res3 = await conn.execute(text(q3))
        dups = res3.fetchall()
        duplicate_count = len(dups)
        print(f"Mükerrer kayıt sayısı:    {duplicate_count}")
        results["duplicate_count"] = duplicate_count

        # 4. Negatif spread sayısı (ask.close < bid.close)
        q4 = """
        SELECT COUNT(*) 
        FROM candles a 
        JOIN candles b ON a.open_time = b.open_time 
          AND a.instrument_id = b.instrument_id 
          AND a.timeframe = b.timeframe 
          AND a.source_id = b.source_id 
          AND a.revision = b.revision
        WHERE a.instrument_id = 'XAUUSD' 
          AND a.timeframe = 60 
          AND a.source_id = 'market_data_lab_m1' 
          AND a.revision = 0
          AND a.price_type = 'ASK' 
          AND b.price_type = 'BID' 
          AND a.close < b.close;
        """
        res4 = await conn.execute(text(q4))
        negative_spread_count = res4.scalar()
        print(f"Negatif spread sayısı:    {negative_spread_count}")
        results["negative_spread_count"] = negative_spread_count

        # 5. ASK-only kayıtları (BID karşılığı olmayan ASK kayıtları)
        q5 = """
        SELECT a.open_time, a.price_type, a.open, a.high, a.low, a.close 
        FROM candles a 
        LEFT JOIN candles b ON a.open_time = b.open_time 
          AND a.instrument_id = b.instrument_id 
          AND a.timeframe = b.timeframe 
          AND a.source_id = b.source_id 
          AND a.revision = b.revision 
          AND b.price_type = 'BID'
        WHERE a.instrument_id = 'XAUUSD' 
          AND a.timeframe = 60 
          AND a.source_id = 'market_data_lab_m1' 
          AND a.revision = 0 
          AND a.price_type = 'ASK' 
          AND b.open_time IS NULL;
        """
        res5 = await conn.execute(text(q5))
        ask_only_rows = res5.fetchall()
        print(f"ASK-only kayıt sayısı:    {len(ask_only_rows)}")
        for r in ask_only_rows:
            print(f"  - Zaman: {r[0]}, Fiyat: O={r[2]}, H={r[3]}, L={r[4]}, C={r[5]}")
        results["ask_only_rows"] = ask_only_rows

        # 6. BID-only kayıtları (ASK karşılığı olmayan BID kayıtları)
        q6 = """
        SELECT b.open_time, b.price_type, b.open, b.high, b.low, b.close 
        FROM candles b 
        LEFT JOIN candles a ON b.open_time = a.open_time 
          AND b.instrument_id = a.instrument_id 
          AND b.timeframe = a.timeframe 
          AND b.source_id = a.source_id 
          AND b.revision = a.revision 
          AND a.price_type = 'ASK'
        WHERE b.instrument_id = 'XAUUSD' 
          AND b.timeframe = 60 
          AND b.source_id = 'market_data_lab_m1' 
          AND b.revision = 0 
          AND b.price_type = 'BID' 
          AND a.open_time IS NULL;
        """
        res6 = await conn.execute(text(q6))
        bid_only_rows = res6.fetchall()
        print(f"BID-only kayıt sayısı:    {len(bid_only_rows)}")
        results["bid_only_count"] = len(bid_only_rows)

        # Assertions
        assert bid_count == 1_002_412
        assert ask_count == 1_002_413
        assert total_count == 2_004_825
        assert synthetic_count == 0
        assert duplicate_count == 0
        assert negative_spread_count == 0
        assert len(ask_only_rows) == 1
        assert str(ask_only_rows[0][0]) == "2026-08-16 23:59:00+00:00"
        assert len(bid_only_rows) == 0
        print("\n>>> BÜTÜN POSTGRESQL DOĞRULAMA KRİTERLERİ %100 GEÇTİ! <<<")

    return results

if __name__ == "__main__":
    asyncio.run(run_sql_verifications())
