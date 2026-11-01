import asyncio
from datetime import UTC, datetime

from traderlab.core.types import DataSourceId, InstrumentId, PitPolicy, PriceType, Timeframe
from traderlab.infrastructure.calendars.forex_session import XAUUSDTradingCalendar
from traderlab.infrastructure.database.repositories.candle_repository import (
    PostgresCandleRepository,
)
from traderlab.infrastructure.database.session import create_db_engine, get_session_factory


async def main() -> None:
    engine = create_db_engine()
    factory = get_session_factory(engine)
    repo = PostgresCandleRepository(factory)
    t0 = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2024, 2, 1, 0, 0, tzinfo=UTC)
    bars = await repo.get_candles(
        DataSourceId("market_data_lab_m1"),
        Timeframe.M1,
        t0,
        t1,
        instrument_id=InstrumentId("XAUUSD"),
        price_type=PriceType.BID,
        policy=PitPolicy.RESEARCH_ESTIMATED,
    )
    cal = XAUUSDTradingCalendar()
    ts = [b.open_time for b in bars]
    unexp = [t for t in ts if not cal.is_market_open(t)]
    dups = len(ts) - len(set(ts))
    print("PG BID Count:", len(bars))
    print("PG Unexpected Closed Bars:", len(unexp))
    print("PG Duplicates:", dups)


if __name__ == "__main__":
    asyncio.run(main())
