"""Deterministic Point-in-Time Event Clock for backtest coordination.

Ensures strict chronological ordering of simulation events and deterministic
tie-breaking across equal timestamps.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterator
from datetime import datetime

from traderlab.domain.backtesting.backtest_models import BacktestEvent, BacktestEventType

# Deterministic priority ranking for events sharing the exact same available_at_utc:
EVENT_TYPE_PRIORITY: dict[BacktestEventType, int] = {
    BacktestEventType.MARKET_BAR_AVAILABLE: 10,
    BacktestEventType.STOP_LOSS_TRIGGERED: 20,
    BacktestEventType.TAKE_PROFIT_TRIGGERED: 21,
    BacktestEventType.POSITION_CLOSED: 25,
    BacktestEventType.SIMULATED_ORDER_FILLED: 30,
    BacktestEventType.SIMULATED_ORDER_EXPIRED: 35,
    BacktestEventType.SIMULATED_ORDER_CANCELLED: 36,
    BacktestEventType.PORTFOLIO_RISK_RELEASED: 38,
    BacktestEventType.STRUCTURAL_EVENT_AVAILABLE: 40,
    BacktestEventType.ICT_SETUP_AVAILABLE: 50,
    BacktestEventType.TRADE_PLAN_AVAILABLE: 60,
    BacktestEventType.RISK_ASSESSMENT_AVAILABLE: 70,
    BacktestEventType.STRATEGY_SELECTION_AVAILABLE: 80,
    BacktestEventType.SIMULATED_ORDER_SUBMITTED: 90,
    BacktestEventType.SIMULATED_ORDER_ELIGIBLE: 95,
    BacktestEventType.SIMULATED_ORDER_REJECTED: 96,
    BacktestEventType.SIMULATION_END: 999,
}


class DeterministicEventQueue:
    """Priority queue enforcing Point-in-Time event causality and idempotence."""

    def __init__(self) -> None:
        self._heap: list[tuple[datetime, int, int, BacktestEvent]] = []
        self._queued_event_ids: set[str] = set()
        self._processed_event_ids: set[str] = set()
        self._sequence_counter: int = 0

    def push(self, event: BacktestEvent) -> bool:
        """Push a backtest event into the queue.

        Returns False if the event was already processed or queued (idempotence).
        """
        if event.event_id in self._queued_event_ids or event.event_id in self._processed_event_ids:
            return False

        self._queued_event_ids.add(event.event_id)
        type_prio = EVENT_TYPE_PRIORITY.get(event.event_type, 100)
        self._sequence_counter += 1
        # Heap tuple: (available_at_utc, type_priority, sequence, event)
        heapq.heappush(
            self._heap,
            (event.available_at_utc, type_prio, self._sequence_counter, event),
        )
        return True

    def pop(self) -> BacktestEvent | None:
        """Pop the earliest available event. Marks event as processed."""
        if not self._heap:
            return None
        _, _, _, event = heapq.heappop(self._heap)
        self._processed_event_ids.add(event.event_id)
        return event

    def peek(self) -> BacktestEvent | None:
        """Return the next event without removing it."""
        if not self._heap:
            return None
        return self._heap[0][3]

    def __len__(self) -> int:
        return len(self._heap)

    def is_processed(self, event_id: str) -> bool:
        return event_id in self._processed_event_ids

    def drain(self) -> Iterator[BacktestEvent]:
        """Iteratively yield and pop all events in deterministic chronological order."""
        while self._heap:
            event = self.pop()
            if event is not None:
                yield event
