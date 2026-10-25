import asyncio
import sys
from pathlib import Path
from decimal import Decimal
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from traderlab.infrastructure.database.session import create_db_engine

# Timestamps to inspect
TIMESTAMPS = [
    # 1. Ocak 2024 normal işlem günü
    ("2024-01-08 12:00:00", "2024_01", "Ocak 2024 Normal İşlem Günü (Londra Seansı)"),
    # 2. 25 Aralık 2024 Noel açılışı
    ("2024-12-25 23:00:00", "2024_12", "25 Aralık 2024 Noel Akşamı İlk Açılış Barı"),
    # 3. 25 Aralık 2024 Noel aktif seansı (düzeltilen meşru flat bar)
    ("2024-12-25 23:47:00", "2024_12", "25 Aralık 2024 Noel Açık Piyasa Düşük Volatilite (Flat BID)"),
    # 4. 25 Aralık 2025 Noel açılışı
    ("2025-12-25 23:00:00", "2025_12", "25 Aralık 2025 Noel Akşamı İlk Açılış Barı"),
    # 5. 2024 Yaz normal işlem günü (ABD TÜFE saati)
    ("2024-06-12 14:30:00", "2024_06", "12 Haziran 2024 Normal İşlem Günü (Volatilite Barı)"),
    # 6. 2025 Normal işlem günü
    ("2025-07-16 10:15:00", "2025_07", "16 Temmuz 2025 Normal İşlem Günü"),
    # 7. 2026 Normal işlem günü
    ("2026-04-15 13:45:00", "2026_04", "15 Nisan 2026 Normal İşlem Günü"),
    # 8. 16 Ağustos 2026 ASK-only kaydı
    ("2026-08-16 23:59:00", "2026_08", "16 Ağustos 2026 Tek Taraflı (ASK-only) Kotasyon"),
    # 9. 7 Eylül 2026 Labor Day erken kapanış öncesi son bar
    ("2026-09-07 18:28:00", "2026_09", "7 Eylül 2026 ABD Labor Day Erken Kapanış Son Barı"),
    # 10. 30 Eylül 2026 seans boşluğu öncesi bar
    ("2026-09-30 22:27:00", "2026_09", "30 Eylül 2026 Boşluk Öncesi Bar"),
    # 11. 30 Eylül 2026 seans boşluğu sonrası bar
    ("2026-09-30 22:29:00", "2026_09", "30 Eylül 2026 Boşluk Sonrası Bar"),
    # 12. Veri arşivinin en son dakikası
    ("2026-10-19 13:59:00", "2026_10", "19 Ekim 2026 Arşiv Bitiş Sınırı (Son Bar)"),
]

async def compare_csv_with_postgres():
    engine = create_db_engine()
    bid_base = Path("../Market-Data-Lab/xauusd/bid/m1")
    ask_base = Path("../Market-Data-Lab/xauusd/ask/m1")

    comparison_rows = []

    async with engine.connect() as conn:
        for dt_str, month_str, desc in TIMESTAMPS:
            ts_utc = pd.Timestamp(dt_str, tz="UTC")
            ts_ms = int(ts_utc.timestamp() * 1000)

            # Read raw CSV
            b_csv = bid_base / f"xauusd_bid_m1_{month_str}.csv"
            a_csv = ask_base / f"xauusd_ask_m1_{month_str}.csv"

            df_b = pd.read_csv(b_csv)
            df_a = pd.read_csv(a_csv)

            raw_b = df_b[df_b["timestamp"] == ts_ms]
            raw_a = df_a[df_a["timestamp"] == ts_ms]

            # Query PostgreSQL
            q = """
            SELECT price_type, open, high, low, close 
            FROM candles 
            WHERE instrument_id = 'XAUUSD' 
              AND timeframe = 60 
              AND source_id = 'market_data_lab_m1' 
              AND revision = 0 
              AND open_time = :dt;
            """
            res = await conn.execute(text(q), {"dt": ts_utc.to_pydatetime()})
            db_rows = {r[0]: r for r in res.fetchall()}

            for p_type, raw_df in [("BID", raw_b), ("ASK", raw_a)]:
                if len(raw_df) == 0:
                    raw_ohlc = "YOK (Ham CSV'de Bulunmuyor)"
                else:
                    r = raw_df.iloc[0]
                    raw_ohlc = f"O={r['open']:.3f}, H={r['high']:.3f}, L={r['low']:.3f}, C={r['close']:.3f}"

                if p_type not in db_rows:
                    db_ohlc = "YOK (DB'de Bulunmuyor)"
                else:
                    d = db_rows[p_type]
                    db_ohlc = f"O={d[1]:.3f}, H={d[2]:.3f}, L={d[3]:.3f}, C={d[4]:.3f}"

                # Match status
                if len(raw_df) == 0 and p_type not in db_rows:
                    match_status = "BİREBİR EŞLEŞTİ (Her İkisinde de Yok)"
                elif len(raw_df) > 0 and p_type in db_rows:
                    r = raw_df.iloc[0]
                    d = db_rows[p_type]
                    exact = (
                        Decimal(str(r['open'])) == d[1] and
                        Decimal(str(r['high'])) == d[2] and
                        Decimal(str(r['low'])) == d[3] and
                        Decimal(str(r['close'])) == d[4]
                    )
                    match_status = "BİREBİR EŞLEŞTİ (TAM SAYISAL EŞİTLİK)" if exact else "UYUŞMAZLIK!"
                else:
                    match_status = "UYUŞMAZLIK (Biri var biri yok)!"

                comparison_rows.append({
                    "timestamp_utc": dt_str + " UTC",
                    "price_type": p_type,
                    "case_description": desc,
                    "raw_csv_ohlc": raw_ohlc,
                    "postgres_ohlc": db_ohlc,
                    "match_status": match_status
                })

    df_out = pd.DataFrame(comparison_rows)
    out_csv = Path("docs/reports/phase_1c_raw_csv_vs_postgres_comparison.csv")
    df_out.to_csv(out_csv, index=False)
    print(f"Comparison saved to {out_csv}")
    print("\n=== SUMMARY OF COMPARISONS ===")
    print(df_out[["timestamp_utc", "price_type", "match_status"]].to_string())

    mismatches = df_out[~df_out["match_status"].str.contains("BİREBİR EŞLEŞTİ")]
    print(f"\nTotal Mismatches: {len(mismatches)}")
    assert len(mismatches) == 0, f"Found {len(mismatches)} mismatches!"
    print(">>> TÜM 12 ZAMAN DAMGASI (24 KOTASYON) HAM CSV İLE POSTGRESQL ARASINDA BİREBİR EŞLEŞTİ! <<<")

if __name__ == "__main__":
    asyncio.run(compare_csv_with_postgres())
