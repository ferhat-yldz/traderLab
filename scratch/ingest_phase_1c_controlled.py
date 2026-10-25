import asyncio
import sys
import time
from datetime import datetime, UTC
from pathlib import Path
from decimal import Decimal
import pandas as pd
from sqlalchemy import text, select, func
from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from traderlab.infrastructure.database.session import create_db_engine, get_session_factory
from traderlab.infrastructure.database.models import CandleModel
from traderlab.infrastructure.providers.market_data_lab.csv_reader import read_m1_csv
from traderlab.infrastructure.providers.market_data_lab.bar_classifier import classify_m1_series, BarQualityClassification
from traderlab.infrastructure.calendars.forex_session import XAUUSDTradingCalendar
from traderlab.core.types import PriceType

CHUNK_SIZE = 2000
SOURCE_ID = "market_data_lab_m1"
INSTRUMENT_ID = "XAUUSD"
TIMEFRAME_M1 = 60

async def backup_database(engine):
    """Rule 1: Create a snapshot backup table before any data ingestion."""
    print("--> [RULE 1] Taking database snapshot backup...")
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS candles_backup_pre_phase1c;"))
        await conn.execute(text("CREATE TABLE candles_backup_pre_phase1c AS SELECT * FROM candles;"))
        res = await conn.execute(text("SELECT COUNT(*) FROM candles_backup_pre_phase1c;"))
        cnt = res.scalar()
        print(f"    Backup table 'candles_backup_pre_phase1c' created with {cnt} rows.")
        await conn.execute(text("DELETE FROM candles WHERE instrument_id = 'XAUUSD';"))

async def ingest_month(session, month_str, bid_file, ask_file, calendar):
    """Read, classify, filter synthetic, and bulk insert one month of M1 bars."""
    bars_b = read_m1_csv(bid_file, PriceType.BID)
    bars_a = read_m1_csv(ask_file, PriceType.ASK)
    
    cb_b = classify_m1_series(bars_b, calendar=calendar)
    cb_a = classify_m1_series(bars_a, calendar=calendar)
    
    # Filter synthetic carry-forward records (Rule 4: Zero synthetic inserted)
    active_b = [b.bar for b in cb_b if b.classification != BarQualityClassification.SYNTHETIC_CARRY_FORWARD]
    active_a = [a.bar for a in cb_a if a.classification != BarQualityClassification.SYNTHETIC_CARRY_FORWARD]
    
    records = []
    
    for bar in active_b:
        records.append({
            "instrument_id": INSTRUMENT_ID,
            "source_id": SOURCE_ID,
            "timeframe": TIMEFRAME_M1,
            "open_time": bar.open_time,
            "close_time": bar.close_time,
            "price_type": "BID",
            "revision": 0,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": None,
            "tick_volume": None,
            "spread": None,
            "is_closed": True,
            "is_synthetic": False,
            "published_at": None,
            "received_at": None,
            "available_at": None,
            "availability_confidence": "UNKNOWN",
        })
        
    for bar in active_a:
        records.append({
            "instrument_id": INSTRUMENT_ID,
            "source_id": SOURCE_ID,
            "timeframe": TIMEFRAME_M1,
            "open_time": bar.open_time,
            "close_time": bar.close_time,
            "price_type": "ASK",
            "revision": 0,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": None,
            "tick_volume": None,
            "spread": None,
            "is_closed": True,
            "is_synthetic": False,
            "published_at": None,
            "received_at": None,
            "available_at": None,
            "availability_confidence": "UNKNOWN",
        })
        
    # Bulk insert in chunks with ON CONFLICT DO NOTHING (Rule 5: Idempotency)
    for i in range(0, len(records), CHUNK_SIZE):
        chunk = records[i:i + CHUNK_SIZE]
        stmt = insert(CandleModel).values(chunk)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["instrument_id", "source_id", "timeframe", "open_time", "price_type", "revision"]
        )
        await session.execute(stmt)
        
    return len(active_b), len(active_a)

async def verify_database(engine):
    """Rule 7: Full database reconciliation and verification against expected totals."""
    print("\n--> [RULE 7] Running database post-ingestion verification queries...")
    async with engine.connect() as conn:
        # Total counts by price_type for XAUUSD
        res = await conn.execute(text("""
            SELECT price_type, COUNT(*) 
            FROM candles 
            WHERE instrument_id = 'XAUUSD' 
            GROUP BY price_type 
            ORDER BY price_type;
        """))
        counts = dict(res.fetchall())
        bid_cnt = counts.get("BID", 0)
        ask_cnt = counts.get("ASK", 0)
        total_xauusd = bid_cnt + ask_cnt
        
        print(f"    PostgreSQL Stored BID Rows:   {bid_cnt:,}")
        print(f"    PostgreSQL Stored ASK Rows:   {ask_cnt:,}")
        print(f"    PostgreSQL Total M1 Rows:     {total_xauusd:,}")
        
        # Check date range
        res_dates = await conn.execute(text("""
            SELECT MIN(open_time), MAX(open_time) 
            FROM candles 
            WHERE instrument_id = 'XAUUSD';
        """))
        min_dt, max_dt = res_dates.fetchone()
        print(f"    Earliest Stored Candle (UTC): {min_dt}")
        print(f"    Latest Stored Candle (UTC):   {max_dt}")
        
        # Check duplicate timestamps
        res_dup = await conn.execute(text("""
            SELECT open_time, price_type, COUNT(*) 
            FROM candles 
            WHERE instrument_id = 'XAUUSD' 
            GROUP BY open_time, price_type 
            HAVING COUNT(*) > 1;
        """))
        dups = res_dup.fetchall()
        print(f"    Duplicate Timestamps in DB:   {len(dups)}")
        
        # Check inverted spreads (ASK < BID)
        res_spread = await conn.execute(text("""
            SELECT COUNT(*) 
            FROM candles a 
            JOIN candles b ON a.open_time = b.open_time 
                AND a.instrument_id = b.instrument_id 
                AND a.timeframe = b.timeframe 
            WHERE a.instrument_id = 'XAUUSD' 
                AND a.price_type = 'ASK' 
                AND b.price_type = 'BID' 
                AND a.close < b.close;
        """))
        inv_spread_cnt = res_spread.scalar()
        print(f"    Inverted Spread Anomalies:    {inv_spread_cnt}")
        
        # Check 16 August 2026 ASK-only record
        res_aug16 = await conn.execute(text("""
            SELECT price_type, open, close 
            FROM candles 
            WHERE instrument_id = 'XAUUSD' 
                AND open_time = '2026-08-16 23:59:00+00';
        """))
        aug16_rows = res_aug16.fetchall()
        print(f"    2026-08-16 23:59:00 UTC Rows: {aug16_rows}")
        
        # Assertions
        assert bid_cnt == 1_002_412, f"BID count {bid_cnt} != 1002412"
        assert ask_cnt == 1_002_413, f"ASK count {ask_cnt} != 1002413"
        assert total_xauusd == 2_004_825, f"Total count {total_xauusd} != 2004825"
        assert len(dups) == 0, "Duplicates found in database!"
        assert inv_spread_cnt == 0, "Inverted spreads found in database!"
        assert len(aug16_rows) == 1 and aug16_rows[0][0] == "ASK", "ASK-only record mismatch!"
        print("\n    >>> ALL DATABASE RECONCILIATION ASSERTIONS PASSED WITH 100% SUCCESS! <<<")

async def main():
    engine = create_db_engine()
    factory = get_session_factory(engine)
    calendar = XAUUSDTradingCalendar()
    
    bid_dir = Path("../Market-Data-Lab/xauusd/bid/m1")
    ask_dir = Path("../Market-Data-Lab/xauusd/ask/m1")
    bid_files = sorted(bid_dir.glob("xauusd_bid_m1_*.csv"))
    
    # 1. Rule 1: Backup
    await backup_database(engine)
    
    # 2. Rule 2: Pilot Batch (Month 1: 2024-01)
    print("\n--> [RULE 2] Ingesting Pilot Batch (Month 1: 2024_01)...")
    t0 = time.time()
    b_file_pilot = bid_files[0]
    a_file_pilot = ask_dir / f"xauusd_ask_m1_2024_01.csv"
    
    async with factory() as session:
        async with session.begin():
            b_cnt, a_cnt = await ingest_month(session, "2024_01", b_file_pilot, a_file_pilot, calendar)
            
    # Verify pilot in DB
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT price_type, COUNT(*) FROM candles WHERE instrument_id = 'XAUUSD' GROUP BY price_type;"))
        pilot_counts = dict(res.fetchall())
        print(f"    Pilot Month 2024_01 Stored in DB: BID={pilot_counts.get('BID', 0)}, ASK={pilot_counts.get('ASK', 0)}")
        assert pilot_counts.get("BID") == 30570, f"Pilot BID {pilot_counts.get('BID')} != 30570"
        assert pilot_counts.get("ASK") == 30570, f"Pilot ASK {pilot_counts.get('ASK')} != 30570"
    print(f"    Pilot Batch Verified Successfully in {time.time() - t0:.2f}s!")
    
    # 3. Full Ingestion (Remaining 33 months)
    print(f"\n--> Ingesting Remaining 33 Months (2024_02 to 2026_10)...")
    t_start_full = time.time()
    total_bid = b_cnt
    total_ask = a_cnt
    
    for b_file in bid_files[1:]:
        month_str = b_file.stem.replace("xauusd_bid_m1_", "")
        a_file = ask_dir / f"xauusd_ask_m1_{month_str}.csv"
        
        m_t0 = time.time()
        async with factory() as session:
            async with session.begin():
                mb_cnt, ma_cnt = await ingest_month(session, month_str, b_file, a_file, calendar)
                total_bid += mb_cnt
                total_ask += ma_cnt
                print(f"    [{month_str}] Ingested Active BID={mb_cnt:,}, ASK={ma_cnt:,} (Total: {mb_cnt + ma_cnt:,}) in {time.time() - m_t0:.2f}s")
                
    print(f"\n--> All 34 Months Ingested in {time.time() - t_start_full:.2f}s!")
    print(f"    Cumulative BID: {total_bid:,} | Cumulative ASK: {total_ask:,} | Total M1: {total_bid + total_ask:,}")
    
    # 4. Verification
    await verify_database(engine)

if __name__ == "__main__":
    asyncio.run(main())
