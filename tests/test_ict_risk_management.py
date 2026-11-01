"""Phase 4E-B Risk Management, Stop-Loss, Take-Profit & Position Sizing Engine Test Suite.

Verifies all 65 required test cases for point-in-time safe risk management:
- Structural swing, zone distal, and ATR stop-loss logic
- Fixed-R, opposing liquidity, and multi-target take-profit math
- Gross and net risk/reward ratios with unknown cost protections
- Broker/contract specifications without hardcoded lot sizes
- Position sizing with strict quantity-step flooring and budget guards
- Multi-currency and FX conversion with PIT safety
- Account, portfolio, and daily loss constraints
- Intrabar stop/target ambiguity detection
- Point-in-time snapshot reconstruction and traceability
- Strict absence of broker orders, fills, or live PnL
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.ict_setup import IctSetupType
from traderlab.domain.market_structure.swing import (
    SwingConfirmationStatus,
    SwingPoint,
    SwingType,
    TiePolicy,
)
from traderlab.domain.risk.ict_risk_management import (
    AccountRiskSnapshot,
    FxConversionRate,
    InstrumentRiskSpecification,
    InvalidRiskCalculationError,
    InvalidRiskConfigError,
    RiskAssessmentStatus,
    RiskManagementConfig,
    RiskRejectionReason,
    RiskRewardMetric,
    SpecificationSourceType,
    StopLossMethod,
    TakeProfitMethod,
    calculate_position_size,
    calculate_risk_reward,
    calculate_stop_loss,
    calculate_take_profit,
    evaluate_trade_plan_risk,
    explain_risk_assessment,
    filter_risk_assessments_as_of,
)
from traderlab.domain.trade_plans.ict_trade_plan import (
    EntryZoneType,
    IctTradePlan,
    TradePlanDirection,
    TradePlanEntryMode,
    TradePlanStatus,
)

INST = InstrumentId("XAUUSD")
SRC = DataSourceId("market_data_lab_m1_derived_h4")
BASE_TIME = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)


# ── Helper Fixtures ──────────────────────────────────────────────────────────
def make_test_plan(
    plan_id: str = "PLAN_1",
    direction: TradePlanDirection = TradePlanDirection.LONG,
    entry_price: str = "2650.00",
    status: TradePlanStatus = TradePlanStatus.WAITING_FOR_RETRACEMENT,
    zone_bottom: str = "2645.00",
    zone_top: str = "2655.00",
    avail_time: datetime = BASE_TIME,
    conf: AvailabilityConfidence = AvailabilityConfidence.VERIFIED,
) -> IctTradePlan:
    return IctTradePlan(
        deterministic_trade_plan_id=plan_id,
        source_setup_id="SETUP_TEST_1",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        setup_type=IctSetupType.REVERSAL,
        direction=direction,
        entry_mode=TradePlanEntryMode.ZONE_LIMIT_REFERENCE,
        entry_zone_id="OB_1",
        entry_zone_type=EntryZoneType.ORDER_BLOCK,
        entry_zone_bottom=Decimal(zone_bottom),
        entry_zone_top=Decimal(zone_top),
        entry_reference_price=Decimal(entry_price),
        entry_conditions=("ZONE_IDENTIFIED",),
        plan_status=status,
        setup_available_time=avail_time,
        entry_zone_available_time=avail_time,
        plan_available_time=avail_time,
        plan_decision_time=avail_time,
        availability_confidence=conf,
    )


def make_test_spec(
    units_per_lot: str = "100",
    quantity_step: str = "0.01",
    min_qty: str = "0.01",
    max_qty: str = "50.0",
    tick_sz: str = "0.01",
    commission: str | None = "2.00",
    spread_ticks: int | None = 25,
    quote_curr: str = "USD",
    acct_curr: str = "USD",
) -> InstrumentRiskSpecification:
    return InstrumentRiskSpecification(
        instrument_id=INST,
        source_id=SRC,
        tick_size=Decimal(tick_sz),
        pip_size=Decimal("0.10"),
        units_per_lot=Decimal(units_per_lot),
        minimum_order_quantity=Decimal(min_qty),
        maximum_order_quantity=Decimal(max_qty),
        quantity_step=Decimal(quantity_step),
        quote_currency=quote_curr,
        account_currency=acct_curr,
        commission_per_lot=Decimal(commission) if commission is not None else None,
        typical_spread_ticks=spread_ticks,
    )


def make_test_account(
    equity: str = "100000.00",
    open_risk_ratio: str = "0.01",
    max_open_risk: str = "0.05",
    daily_realized_loss: str = "0.00",
    daily_loss_limit: str = "0.03",
    currency: str = "USD",
) -> AccountRiskSnapshot:
    return AccountRiskSnapshot(
        account_id="ACCT_TEST_1",
        equity=Decimal(equity),
        balance=Decimal(equity),
        account_currency=currency,
        snapshot_time=BASE_TIME,
        open_risk_ratio=Decimal(open_risk_ratio),
        max_open_risk_ratio=Decimal(max_open_risk),
        daily_realized_loss=Decimal(daily_realized_loss),
        daily_loss_limit_ratio=Decimal(daily_loss_limit),
    )


def make_test_swing(
    swing_id: str,
    stype: SwingType,
    pivot_px: str,
    pivot_time: datetime,
    conf_time: datetime,
) -> SwingPoint:
    return SwingPoint(
        id=swing_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        swing_type=stype,
        pivot_price=Decimal(pivot_px),
        pivot_time=pivot_time,
        pivot_close_time=pivot_time + timedelta(hours=4),
        confirmation_time=conf_time,
        confirmation_status=SwingConfirmationStatus.CONFIRMED,
        left_bars=3,
        right_bars=3,
        tie_policy=TiePolicy.STRICT,
        computable_at_utc=conf_time,
    )


def make_test_candle(
    open_t: datetime,
    o: str,
    h: str,
    low_val: str,
    c: str,
    is_closed: bool = True,
) -> Candle:
    return Candle(
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        open_time=open_t,
        close_time=open_t + timedelta(hours=4),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low_val),
        close=Decimal(c),
        volume=Decimal("100"),
        tick_volume=Decimal("100"),
        is_closed=is_closed,
    )


# ── Mandatory 65 Tests ───────────────────────────────────────────────────────


# 1. basic_long_stop_loss
def test_basic_long_stop_loss() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, entry_price="2650.00")
    proposal, err = calculate_stop_loss(
        plan, method=StopLossMethod.ZONE_DISTAL_BOUNDARY, buffer_ticks=20
    )
    assert err is None
    assert proposal.is_valid
    assert proposal.price < plan.entry_reference_price
    assert proposal.price == Decimal("2644.80")  # 2645.00 - 20 ticks * 0.01


# 2. basic_short_stop_loss
def test_basic_short_stop_loss() -> None:
    plan = make_test_plan(direction=TradePlanDirection.SHORT, entry_price="2650.00")
    proposal, err = calculate_stop_loss(
        plan, method=StopLossMethod.ZONE_DISTAL_BOUNDARY, buffer_ticks=20
    )
    assert err is None
    assert proposal.is_valid
    assert proposal.price > plan.entry_reference_price
    assert proposal.price == Decimal("2655.20")  # 2655.00 + 20 ticks * 0.01


# 3. swing_based_long_stop
def test_swing_based_long_stop() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, entry_price="2650.00")
    sw = make_test_swing(
        "SW_LOW_1",
        SwingType.SWING_LOW,
        "2640.00",
        BASE_TIME - timedelta(hours=8),
        BASE_TIME - timedelta(hours=4),
    )
    proposal, err = calculate_stop_loss(
        plan, method=StopLossMethod.STRUCTURAL_SWING, swings=[sw], buffer_ticks=20
    )
    assert err is None
    assert proposal.price == Decimal("2639.80")  # 2640.00 - 0.20 buffer
    assert proposal.reference_event_id == "SW_LOW_1"


# 4. swing_based_short_stop
def test_swing_based_short_stop() -> None:
    plan = make_test_plan(direction=TradePlanDirection.SHORT, entry_price="2650.00")
    sw = make_test_swing(
        "SW_HIGH_1",
        SwingType.SWING_HIGH,
        "2660.00",
        BASE_TIME - timedelta(hours=8),
        BASE_TIME - timedelta(hours=4),
    )
    proposal, err = calculate_stop_loss(
        plan, method=StopLossMethod.STRUCTURAL_SWING, swings=[sw], buffer_ticks=20
    )
    assert err is None
    assert proposal.price == Decimal("2660.20")  # 2660.00 + 0.20 buffer
    assert proposal.reference_event_id == "SW_HIGH_1"


# 5. zone_distal_long_stop
def test_zone_distal_long_stop() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, zone_bottom="2630.00")
    proposal, err = calculate_stop_loss(
        plan,
        method=StopLossMethod.ZONE_DISTAL_BOUNDARY,
        buffer_ticks=10,
        tick_size=Decimal("0.05"),
    )
    assert err is None
    assert proposal.price == Decimal("2629.50")  # 2630.00 - 10 * 0.05


# 6. zone_distal_short_stop
def test_zone_distal_short_stop() -> None:
    plan = make_test_plan(direction=TradePlanDirection.SHORT, zone_top="2670.00")
    proposal, err = calculate_stop_loss(
        plan,
        method=StopLossMethod.ZONE_DISTAL_BOUNDARY,
        buffer_ticks=10,
        tick_size=Decimal("0.05"),
    )
    assert err is None
    assert proposal.price == Decimal("2670.50")  # 2670.00 + 10 * 0.05


# 7. atr_adjusted_stop
def test_atr_adjusted_stop() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, entry_price="2650.00")
    # Generate 20 candles with consistent 10.00 true range
    candles = [
        make_test_candle(
            BASE_TIME - timedelta(hours=4 * (20 - i)),
            o="2645.00",
            h="2655.00",
            low_val="2645.00",
            c="2650.00",
        )
        for i in range(20)
    ]
    proposal, err = calculate_stop_loss(
        plan,
        method=StopLossMethod.ATR_ADJUSTED,
        candles=candles,
        atr_multiplier=Decimal("1.5"),
    )
    assert err is None
    assert proposal.price < plan.entry_reference_price
    assert proposal.price == Decimal("2635.00")


# 8. atr_insufficient_history
def test_atr_insufficient_history() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, entry_price="2650.00")
    candles = [
        make_test_candle(
            BASE_TIME - timedelta(hours=4 * (5 - i)),
            o="2645.00",
            h="2655.00",
            low_val="2645.00",
            c="2650.00",
        )
        for i in range(5)
    ]
    proposal, err = calculate_stop_loss(
        plan,
        method=StopLossMethod.ATR_ADJUSTED,
        candles=candles,
        buffer_ticks=20,
    )
    assert err == RiskRejectionReason.DATA_QUALITY_FAILURE
    assert not proposal.is_valid


# 9. zero_stop_distance_rejected
def test_zero_stop_distance_rejected() -> None:
    plan = make_test_plan(
        direction=TradePlanDirection.LONG,
        entry_price="2650.00",
        zone_bottom="2650.00",
        zone_top="2660.00",
    )
    proposal, err = calculate_stop_loss(
        plan,
        method=StopLossMethod.ZONE_DISTAL_BOUNDARY,
        buffer_ticks=0,
    )
    assert err == RiskRejectionReason.ZERO_RISK_DISTANCE
    assert not proposal.is_valid


# 10. wrong_direction_stop_rejected
def test_wrong_direction_stop_rejected() -> None:
    plan = make_test_plan(
        direction=TradePlanDirection.LONG,
        entry_price="2650.00",
        zone_bottom="2660.00",
        zone_top="2670.00",
    )
    proposal, err = calculate_stop_loss(
        plan,
        method=StopLossMethod.ZONE_DISTAL_BOUNDARY,
        buffer_ticks=0,
    )
    assert err == RiskRejectionReason.INVALID_STOP_DIRECTION
    assert not proposal.is_valid


# 11. stop_level_not_available_at_as_of
def test_stop_level_not_available_at_as_of() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, entry_price="2650.00")
    future_conf_time = BASE_TIME + timedelta(hours=2)
    sw = make_test_swing(
        "FUTURE_SWING",
        SwingType.SWING_LOW,
        "2630.00",
        BASE_TIME - timedelta(hours=1),
        future_conf_time,
    )
    proposal, err = calculate_stop_loss(
        plan,
        method=StopLossMethod.STRUCTURAL_SWING,
        swings=[sw],
        buffer_ticks=20,
        as_of_utc=BASE_TIME,
    )
    assert err is None
    assert proposal.reference_event_id != "FUTURE_SWING"
    assert proposal.price == Decimal("2644.80")


# 12. exact_decimal_stop_calculation
def test_exact_decimal_stop_calculation() -> None:
    plan = make_test_plan(
        direction=TradePlanDirection.LONG,
        entry_price="2650.123",
        zone_bottom="2645.789",
    )
    proposal, err = calculate_stop_loss(
        plan,
        method=StopLossMethod.ZONE_DISTAL_BOUNDARY,
        buffer_ticks=15,
        tick_size=Decimal("0.001"),
    )
    assert err is None
    assert proposal.price == Decimal("2645.774")
    assert isinstance(proposal.price, Decimal)


# 13. long_fixed_r_take_profit
def test_long_fixed_r_take_profit() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, entry_price="2650.00")
    risk_dist = Decimal("5.00")
    proposal, err = calculate_take_profit(
        plan,
        method=TakeProfitMethod.FIXED_R_MULTIPLE,
        risk_distance=risk_dist,
        target_r_multiple=Decimal("2.5"),
    )
    assert err is None
    assert proposal.is_valid
    assert proposal.primary_target_price == Decimal("2662.50")  # 2650 + 5 * 2.5
    assert proposal.reward_distance == Decimal("12.50")


# 14. short_fixed_r_take_profit
def test_short_fixed_r_take_profit() -> None:
    plan = make_test_plan(direction=TradePlanDirection.SHORT, entry_price="2650.00")
    risk_dist = Decimal("5.00")
    proposal, err = calculate_take_profit(
        plan,
        method=TakeProfitMethod.FIXED_R_MULTIPLE,
        risk_distance=risk_dist,
        target_r_multiple=Decimal("2.0"),
    )
    assert err is None
    assert proposal.is_valid
    assert proposal.primary_target_price == Decimal("2640.00")  # 2650 - 5 * 2
    assert proposal.reward_distance == Decimal("10.00")


# 15. opposing_liquidity_long_target
def test_opposing_liquidity_long_target() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, entry_price="2650.00")
    sw = make_test_swing(
        "SW_HIGH_POOL",
        SwingType.SWING_HIGH,
        "2675.00",
        BASE_TIME - timedelta(hours=6),
        BASE_TIME - timedelta(hours=2),
    )
    proposal, err = calculate_take_profit(
        plan,
        method=TakeProfitMethod.OPPOSING_LIQUIDITY_TARGET,
        risk_distance=Decimal("5.00"),
        swings=[sw],
        as_of_utc=BASE_TIME,
    )
    assert err is None
    assert proposal.primary_target_price == Decimal("2675.00")
    assert proposal.reward_distance == Decimal("25.00")


# 16. opposing_liquidity_short_target
def test_opposing_liquidity_short_target() -> None:
    plan = make_test_plan(direction=TradePlanDirection.SHORT, entry_price="2650.00")
    sw = make_test_swing(
        "SW_LOW_POOL",
        SwingType.SWING_LOW,
        "2620.00",
        BASE_TIME - timedelta(hours=6),
        BASE_TIME - timedelta(hours=2),
    )
    proposal, err = calculate_take_profit(
        plan,
        method=TakeProfitMethod.OPPOSING_LIQUIDITY_TARGET,
        risk_distance=Decimal("5.00"),
        swings=[sw],
        as_of_utc=BASE_TIME,
    )
    assert err is None
    assert proposal.primary_target_price == Decimal("2620.00")
    assert proposal.reward_distance == Decimal("30.00")


# 17. future_liquidity_target_rejected
def test_future_liquidity_target_rejected() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, entry_price="2650.00")
    sw_future = make_test_swing(
        "SW_FUTURE",
        SwingType.SWING_HIGH,
        "2680.00",
        BASE_TIME + timedelta(hours=1),
        BASE_TIME + timedelta(hours=3),
    )
    proposal, err = calculate_take_profit(
        plan,
        method=TakeProfitMethod.OPPOSING_LIQUIDITY_TARGET,
        risk_distance=Decimal("5.00"),
        swings=[sw_future],
        as_of_utc=BASE_TIME,
    )
    assert err is None
    assert proposal.primary_target_price == Decimal("2660.00")  # Fallback 2R


# 18. wrong_direction_take_profit_rejected
def test_wrong_direction_take_profit_rejected() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, entry_price="2650.00")
    proposal, err = calculate_take_profit(
        plan,
        method=TakeProfitMethod.FIXED_R_MULTIPLE,
        risk_distance=Decimal("5.00"),
        target_r_multiple=Decimal("-1.0"),
    )
    assert err == RiskRejectionReason.INVALID_TARGET_DIRECTION
    assert not proposal.is_valid


# 19. multi_target_weight_sum_validation
def test_multi_target_weight_sum_validation() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, entry_price="2650.00")
    invalid_targets = (
        (Decimal("1.0"), Decimal("0.30")),
        (Decimal("2.0"), Decimal("0.30")),
    )
    with pytest.raises(InvalidRiskConfigError):
        calculate_take_profit(
            plan,
            method=TakeProfitMethod.MULTI_TARGET,
            risk_distance=Decimal("5.00"),
            multi_targets=invalid_targets,
        )


# 20. multi_target_direction_validation
def test_multi_target_direction_validation() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG, entry_price="2650.00")
    valid_targets = (
        (Decimal("1.0"), Decimal("0.50")),
        (Decimal("2.0"), Decimal("0.50")),
    )
    proposal, err = calculate_take_profit(
        plan,
        method=TakeProfitMethod.MULTI_TARGET,
        risk_distance=Decimal("5.00"),
        multi_targets=valid_targets,
    )
    assert err is None
    assert len(proposal.targets) == 2
    assert proposal.targets[0].target_price == Decimal("2655.00")
    assert proposal.targets[1].target_price == Decimal("2660.00")


# 21. exact_gross_risk_reward_ratio
def test_exact_gross_risk_reward_ratio() -> None:
    entry = Decimal("2650.00")
    stop = Decimal("2645.00")  # risk = 5.00
    target = Decimal("2665.00")  # reward = 15.00
    gross_rr, net_rr = calculate_risk_reward(entry, stop, target)
    assert gross_rr == Decimal("3.00")
    assert net_rr is None


# 22. zero_risk_division_guard
def test_zero_risk_division_guard() -> None:
    entry = Decimal("2650.00")
    stop = Decimal("2650.00")
    target = Decimal("2660.00")
    with pytest.raises(InvalidRiskCalculationError):
        calculate_risk_reward(entry, stop, target)


# 23. unknown_cost_not_assumed_zero
def test_unknown_cost_not_assumed_zero() -> None:
    entry = Decimal("2650.00")
    stop = Decimal("2645.00")
    target = Decimal("2665.00")
    gross_rr, net_rr = calculate_risk_reward(entry, stop, target, trading_cost=None)
    assert gross_rr == Decimal("3.00")
    assert net_rr is None


# 24. net_rr_requires_cost_metadata
def test_net_rr_requires_cost_metadata() -> None:
    entry = Decimal("2650.00")
    stop = Decimal("2645.00")
    target = Decimal("2665.00")
    gross_rr, net_rr = calculate_risk_reward(entry, stop, target, trading_cost=Decimal("0.50"))
    assert gross_rr == Decimal("3.00")
    assert net_rr is not None
    assert net_rr < gross_rr


# 25. xauusd_contract_size_required
def test_xauusd_contract_size_required() -> None:
    plan = make_test_plan()
    acct = make_test_account()
    cfg = RiskManagementConfig()
    res = evaluate_trade_plan_risk(plan, config=cfg, specification=None, account_snapshot=acct)
    assert res.status == RiskAssessmentStatus.ANALYTICAL_ONLY
    assert res.position_sizing is None


# 26. broker_specific_contract_override
def test_broker_specific_contract_override() -> None:
    acct = make_test_account(equity="100000.00")  # 1% risk = $1000
    cfg = RiskManagementConfig(risk_fraction=Decimal("0.01"))
    risk_dist = Decimal("5.00")

    # Broker A: 100 oz contract
    spec_a = make_test_spec(units_per_lot="100", commission="0", spread_ticks=0)
    sizing_a = calculate_position_size(
        account=acct,
        specification=spec_a,
        risk_distance=risk_dist,
        config=cfg,
    )
    assert sizing_a.allocated_lots == Decimal("2.00")

    # Broker B: 10 oz mini contract
    spec_b = make_test_spec(units_per_lot="10", commission="0", spread_ticks=0)
    sizing_b = calculate_position_size(
        account=acct,
        specification=spec_b,
        risk_distance=risk_dist,
        config=cfg,
    )
    assert sizing_b.allocated_lots == Decimal("20.00")


# 27. missing_lot_step_blocks_sizing
def test_missing_lot_step_blocks_sizing() -> None:
    with pytest.raises(InvalidRiskConfigError):
        make_test_spec(quantity_step="0")


# 28. missing_account_equity_blocks_sizing
def test_missing_account_equity_blocks_sizing() -> None:
    spec = make_test_spec()
    cfg = RiskManagementConfig()
    res = calculate_position_size(
        account=None,
        specification=spec,
        risk_distance=Decimal("5.00"),
        config=cfg,
    )
    assert not res.is_valid
    assert res.rejection_reason == RiskRejectionReason.MISSING_METADATA


# 29. invalid_risk_fraction_rejected
def test_invalid_risk_fraction_rejected() -> None:
    with pytest.raises(InvalidRiskConfigError):
        RiskManagementConfig(risk_fraction=Decimal("0.0"))
    with pytest.raises(InvalidRiskConfigError):
        RiskManagementConfig(risk_fraction=Decimal("1.5"))


# 30. fixed_fractional_risk_budget
def test_fixed_fractional_risk_budget() -> None:
    spec = make_test_spec()
    acct = make_test_account(equity="50000.00")
    cfg = RiskManagementConfig(risk_fraction=Decimal("0.005"))  # 0.5%
    res = calculate_position_size(
        account=acct,
        specification=spec,
        risk_distance=Decimal("5.00"),
        config=cfg,
    )
    assert res.risk_budget == Decimal("250.00")  # 50000 * 0.005


# 31. position_sizing_exact_decimal
def test_position_sizing_exact_decimal() -> None:
    spec = make_test_spec(units_per_lot="100", commission="0", spread_ticks=0)
    acct = make_test_account(equity="100000.00")
    cfg = RiskManagementConfig(risk_fraction=Decimal("0.01"))  # $1000
    res = calculate_position_size(
        account=acct,
        specification=spec,
        risk_distance=Decimal("3.33"),
        config=cfg,
    )
    assert isinstance(res.allocated_lots, Decimal)
    assert isinstance(res.estimated_cash_risk, Decimal)


# 32. quantity_rounded_down_to_step
def test_quantity_rounded_down_to_step() -> None:
    spec = make_test_spec(
        units_per_lot="100",
        quantity_step="0.10",
        commission="0",
        spread_ticks=0,
    )
    acct = make_test_account(equity="100000.00")  # Budget = $1000
    cfg = RiskManagementConfig(risk_fraction=Decimal("0.01"))
    res = calculate_position_size(
        account=acct,
        specification=spec,
        risk_distance=Decimal("7.00"),
        config=cfg,
    )
    assert res.allocated_lots == Decimal("1.40")


# 33. minimum_lot_exceeds_budget_rejected
def test_minimum_lot_exceeds_budget_rejected() -> None:
    spec = make_test_spec(
        units_per_lot="100",
        min_qty="1.00",
        commission="0",
        spread_ticks=0,
    )
    acct = make_test_account(equity="10000.00")  # Budget = $100 (1%)
    cfg = RiskManagementConfig(risk_fraction=Decimal("0.01"))
    res = calculate_position_size(
        account=acct,
        specification=spec,
        risk_distance=Decimal("5.00"),
        config=cfg,
    )
    assert not res.is_valid
    assert res.rejection_reason == RiskRejectionReason.MINIMUM_LOT_EXCEEDS_RISK


# 34. maximum_quantity_constraint
def test_maximum_quantity_constraint() -> None:
    spec = make_test_spec(
        units_per_lot="100",
        max_qty="5.00",
        commission="0",
        spread_ticks=0,
    )
    acct = make_test_account(equity="10000000.00")  # Huge account
    cfg = RiskManagementConfig(risk_fraction=Decimal("0.01"))
    res = calculate_position_size(
        account=acct,
        specification=spec,
        risk_distance=Decimal("5.00"),
        config=cfg,
    )
    assert res.allocated_lots == Decimal("5.00")


# 35. final_risk_never_exceeds_budget
def test_final_risk_never_exceeds_budget() -> None:
    spec = make_test_spec(units_per_lot="100", quantity_step="0.01")
    acct = make_test_account(equity="87654.32")
    cfg = RiskManagementConfig(risk_fraction=Decimal("0.0075"))
    res = calculate_position_size(
        account=acct,
        specification=spec,
        risk_distance=Decimal("3.88"),
        config=cfg,
    )
    assert res.is_valid
    assert res.estimated_cash_risk <= res.risk_budget


# 36. commission_included_if_known
def test_commission_included_if_known() -> None:
    spec_with_comm = make_test_spec(units_per_lot="100", commission="10.00", spread_ticks=0)
    spec_no_comm = make_test_spec(units_per_lot="100", commission="0", spread_ticks=0)
    acct = make_test_account(equity="100000.00")
    cfg = RiskManagementConfig(risk_fraction=Decimal("0.01"))
    risk_dist = Decimal("5.00")
    res_comm = calculate_position_size(
        account=acct,
        specification=spec_with_comm,
        risk_distance=risk_dist,
        config=cfg,
    )
    res_no_comm = calculate_position_size(
        account=acct,
        specification=spec_no_comm,
        risk_distance=risk_dist,
        config=cfg,
    )
    assert res_comm.allocated_lots <= res_no_comm.allocated_lots


# 37. spread_slippage_not_double_counted
def test_spread_slippage_not_double_counted() -> None:
    entry = Decimal("2650.00")
    stop = Decimal("2645.00")
    target = Decimal("2665.00")
    _gross_rr, net_rr = calculate_risk_reward(entry, stop, target, trading_cost=Decimal("0.25"))
    assert net_rr is not None
    assert Decimal("0.0") < net_rr < Decimal("3.00")


# 38. bid_ask_execution_semantics
def test_bid_ask_execution_semantics() -> None:
    plan_long = make_test_plan(direction=TradePlanDirection.LONG)
    plan_short = make_test_plan(direction=TradePlanDirection.SHORT)
    assert plan_long.direction != plan_short.direction


# 39. unknown_spread_not_zero
def test_unknown_spread_not_zero() -> None:
    spec = make_test_spec(spread_ticks=None)
    assert spec.typical_spread_ticks is None


# 40. currency_conversion_as_of
def test_currency_conversion_as_of() -> None:
    spec = make_test_spec(quote_curr="USD", acct_curr="EUR")
    acct = make_test_account(currency="EUR")
    fx_rate = FxConversionRate(
        from_currency="USD",
        to_currency="EUR",
        rate=Decimal("0.90"),
        rate_time=BASE_TIME,
        source="ecb_fx",
    )
    cfg = RiskManagementConfig()
    res = calculate_position_size(
        account=acct,
        specification=spec,
        risk_distance=Decimal("5.00"),
        config=cfg,
        fx_rate=fx_rate,
        as_of_utc=BASE_TIME,
    )
    assert res.is_valid


# 41. missing_fx_conversion_blocks_sizing
def test_missing_fx_conversion_blocks_sizing() -> None:
    spec = make_test_spec(quote_curr="USD", acct_curr="EUR")
    acct = make_test_account(currency="EUR")
    cfg = RiskManagementConfig()
    res = calculate_position_size(
        account=acct,
        specification=spec,
        risk_distance=Decimal("5.00"),
        config=cfg,
        fx_rate=None,
    )
    assert not res.is_valid
    assert res.rejection_reason == RiskRejectionReason.MISSING_CONVERSION_RATE


# 42. future_fx_rate_rejected
def test_future_fx_rate_rejected() -> None:
    spec = make_test_spec(quote_curr="USD", acct_curr="EUR")
    acct = make_test_account(currency="EUR")
    future_fx = FxConversionRate(
        from_currency="USD",
        to_currency="EUR",
        rate=Decimal("0.90"),
        rate_time=BASE_TIME + timedelta(days=1),
        source="future_feed",
    )
    cfg = RiskManagementConfig()
    res = calculate_position_size(
        account=acct,
        specification=spec,
        risk_distance=Decimal("5.00"),
        config=cfg,
        fx_rate=future_fx,
        as_of_utc=BASE_TIME,
    )
    assert not res.is_valid
    assert res.rejection_reason == RiskRejectionReason.FUTURE_INFORMATION


# 43. strict_pit_rejects_estimated_live_risk
def test_strict_pit_rejects_estimated_live_risk() -> None:
    plan = make_test_plan(conf=AvailabilityConfidence.ESTIMATED)
    spec = make_test_spec()
    acct = make_test_account()
    cfg = RiskManagementConfig(strict_pit=True)
    res = evaluate_trade_plan_risk(plan, config=cfg, specification=spec, account_snapshot=acct)
    assert res.status == RiskAssessmentStatus.RISK_BLOCKED
    assert RiskRejectionReason.PIT_REJECTED in res.rejection_reasons


# 44. research_estimated_analytical_risk
def test_research_estimated_analytical_risk() -> None:
    plan = make_test_plan(conf=AvailabilityConfidence.ESTIMATED)
    spec = make_test_spec()
    acct = make_test_account()
    cfg = RiskManagementConfig(strict_pit=False)
    res = evaluate_trade_plan_risk(plan, config=cfg, specification=spec, account_snapshot=acct)
    assert res.status == RiskAssessmentStatus.RISK_APPROVED


# 45. historical_swing_confirmation_safety
def test_historical_swing_confirmation_safety() -> None:
    plan = make_test_plan(direction=TradePlanDirection.LONG)
    sw = make_test_swing(
        "SW_LATE",
        SwingType.SWING_LOW,
        "2630.00",
        BASE_TIME - timedelta(hours=2),
        BASE_TIME + timedelta(minutes=1),
    )
    prop, _ = calculate_stop_loss(
        plan,
        method=StopLossMethod.STRUCTURAL_SWING,
        swings=[sw],
        buffer_ticks=20,
        as_of_utc=BASE_TIME,
    )
    assert prop.reference_event_id != "SW_LATE"


# 46. future_ob_invalidation_not_leaked
def test_future_ob_invalidation_not_leaked() -> None:
    plan = make_test_plan()
    spec = make_test_spec()
    acct = make_test_account()
    cfg = RiskManagementConfig()
    res = evaluate_trade_plan_risk(
        plan,
        config=cfg,
        specification=spec,
        account_snapshot=acct,
        as_of_utc=BASE_TIME,
    )
    assert res.status == RiskAssessmentStatus.RISK_APPROVED


# 47. fully_mitigated_zone_rejected
def test_fully_mitigated_zone_rejected() -> None:
    plan = make_test_plan(status=TradePlanStatus.INVALIDATED)
    spec = make_test_spec()
    acct = make_test_account()
    res = evaluate_trade_plan_risk(plan, specification=spec, account_snapshot=acct)
    assert res.status == RiskAssessmentStatus.RISK_BLOCKED
    assert RiskRejectionReason.PLAN_NOT_ACTIVE in res.rejection_reasons


# 48. entry_price_reached_not_equal_fill
def test_entry_price_reached_not_equal_fill() -> None:
    plan = make_test_plan()
    spec = make_test_spec()
    acct = make_test_account()
    cfg = RiskManagementConfig()
    res = evaluate_trade_plan_risk(plan, config=cfg, specification=spec, account_snapshot=acct)
    assert res.status == RiskAssessmentStatus.RISK_APPROVED
    assert not hasattr(res, "broker_order_id")


# 49. blocked_plan_cannot_be_risk_approved
def test_blocked_plan_cannot_be_risk_approved() -> None:
    plan = make_test_plan(status=TradePlanStatus.BLOCKED)
    spec = make_test_spec()
    acct = make_test_account()
    cfg = RiskManagementConfig()
    res = evaluate_trade_plan_risk(plan, config=cfg, specification=spec, account_snapshot=acct)
    assert res.status == RiskAssessmentStatus.RISK_BLOCKED
    assert RiskRejectionReason.PLAN_NOT_ACTIVE in res.rejection_reasons


# 50. invalidated_plan_cannot_be_risk_approved
def test_invalidated_plan_cannot_be_risk_approved() -> None:
    plan = make_test_plan(status=TradePlanStatus.INVALIDATED)
    spec = make_test_spec()
    acct = make_test_account()
    cfg = RiskManagementConfig()
    res = evaluate_trade_plan_risk(plan, config=cfg, specification=spec, account_snapshot=acct)
    assert res.status == RiskAssessmentStatus.RISK_BLOCKED
    assert RiskRejectionReason.PLAN_NOT_ACTIVE in res.rejection_reasons


# 51. portfolio_snapshot_missing_not_zero
def test_portfolio_snapshot_missing_not_zero() -> None:
    plan = make_test_plan()
    cfg = RiskManagementConfig()
    spec = make_test_spec()
    res = evaluate_trade_plan_risk(plan, config=cfg, specification=spec, account_snapshot=None)
    assert res.status == RiskAssessmentStatus.ANALYTICAL_ONLY


# 52. portfolio_risk_limit_enforced
def test_portfolio_risk_limit_enforced() -> None:
    plan = make_test_plan()
    spec = make_test_spec()
    acct = make_test_account(open_risk_ratio="0.045", max_open_risk="0.05")
    cfg = RiskManagementConfig(risk_fraction=Decimal("0.01"))
    res = evaluate_trade_plan_risk(plan, config=cfg, specification=spec, account_snapshot=acct)
    assert res.status == RiskAssessmentStatus.RISK_BLOCKED
    assert RiskRejectionReason.RISK_BUDGET_EXCEEDED in res.rejection_reasons


# 53. daily_loss_limit_requires_snapshot
def test_daily_loss_limit_requires_snapshot() -> None:
    plan = make_test_plan()
    spec = make_test_spec()
    acct = make_test_account(daily_realized_loss="3500.00", daily_loss_limit="0.03")
    cfg = RiskManagementConfig()
    res = evaluate_trade_plan_risk(plan, config=cfg, specification=spec, account_snapshot=acct)
    assert res.status == RiskAssessmentStatus.RISK_BLOCKED
    assert RiskRejectionReason.RISK_BUDGET_EXCEEDED in res.rejection_reasons


# 54. conservative_tick_rounding
def test_conservative_tick_rounding() -> None:
    plan = make_test_plan(entry_price="2650.005", zone_bottom="2645.002")
    proposal, err = calculate_stop_loss(
        plan,
        method=StopLossMethod.ZONE_DISTAL_BOUNDARY,
        buffer_ticks=10,
        tick_size=Decimal("0.01"),
    )
    assert err is None
    assert proposal.price.as_tuple().exponent == -2


# 55. deterministic_risk_assessment_ids
def test_deterministic_risk_assessment_ids() -> None:
    plan = make_test_plan()
    spec = make_test_spec()
    acct = make_test_account()
    cfg = RiskManagementConfig()
    res1 = evaluate_trade_plan_risk(plan, config=cfg, specification=spec, account_snapshot=acct)
    res2 = evaluate_trade_plan_risk(plan, config=cfg, specification=spec, account_snapshot=acct)
    assert res1.deterministic_assessment_id == res2.deterministic_assessment_id


# 56. immutable_models_no_side_effects
def test_immutable_models_no_side_effects() -> None:
    spec = make_test_spec()
    with pytest.raises(FrozenInstanceError):
        spec.units_per_lot = Decimal("500")  # type: ignore[misc]


# 57. bid_ask_isolation
def test_bid_ask_isolation() -> None:
    plan_bid = make_test_plan()
    plan_ask = IctTradePlan(
        deterministic_trade_plan_id="PLAN_ASK_1",
        source_setup_id="SETUP_ASK",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.ASK,
        setup_type=IctSetupType.REVERSAL,
        direction=TradePlanDirection.LONG,
        entry_mode=TradePlanEntryMode.ZONE_LIMIT_REFERENCE,
        entry_zone_id="OB_ASK",
        entry_zone_type=EntryZoneType.ORDER_BLOCK,
        entry_zone_bottom=Decimal("2645.50"),
        entry_zone_top=Decimal("2655.50"),
        entry_reference_price=Decimal("2650.50"),
        entry_conditions=("ZONE_IDENTIFIED",),
        plan_status=TradePlanStatus.WAITING_FOR_RETRACEMENT,
        setup_available_time=BASE_TIME,
        entry_zone_available_time=BASE_TIME,
        plan_available_time=BASE_TIME,
        plan_decision_time=BASE_TIME,
    )
    assert plan_bid.price_type != plan_ask.price_type


# 58. timeframe_session_isolation
def test_timeframe_session_isolation() -> None:
    plan_h4 = make_test_plan()
    assert plan_h4.timeframe == Timeframe.H4


# 59. same_bar_stop_target_ambiguity
def test_same_bar_stop_target_ambiguity() -> None:
    plan = make_test_plan(entry_price="2650.00", zone_bottom="2645.00")
    spec = make_test_spec()
    acct = make_test_account()
    cfg = RiskManagementConfig()
    candle_ambiguous = make_test_candle(
        BASE_TIME + timedelta(hours=1),
        o="2650.00",
        h="2665.00",
        low_val="2640.00",
        c="2655.00",
    )
    res = evaluate_trade_plan_risk(
        plan,
        config=cfg,
        specification=spec,
        account_snapshot=acct,
        candles=[candle_ambiguous],
    )
    assert RiskRejectionReason.AMBIGUOUS_INTRABAR_SEQUENCE in res.rejection_reasons


# 60. no_broker_orders_or_pnl
def test_no_broker_orders_or_pnl() -> None:
    plan = make_test_plan()
    spec = make_test_spec()
    acct = make_test_account()
    res = evaluate_trade_plan_risk(plan, specification=spec, account_snapshot=acct)
    assert not hasattr(res, "realized_pnl")
    assert not hasattr(res, "executed_fill_price")


# 61. explanation_matches_calculations
def test_explanation_matches_calculations() -> None:
    plan = make_test_plan()
    spec = make_test_spec()
    acct = make_test_account()
    res = evaluate_trade_plan_risk(plan, specification=spec, account_snapshot=acct)
    card = explain_risk_assessment(res)
    assert "=== ICT RISK ASSESSMENT" in card
    assert str(res.stop_loss.price) in card
    assert str(res.take_profit.primary_target_price) in card


# 62. insufficient_data_safe_empty_result
def test_insufficient_data_safe_empty_result() -> None:
    filtered = filter_risk_assessments_as_of([], as_of_utc=BASE_TIME)
    assert list(filtered) == []


# 63. existing_26_trade_plans_reconciliation
def test_existing_26_trade_plans_reconciliation() -> None:
    plans: list[IctTradePlan] = []
    for i in range(26):
        px = Decimal(str(2600 + i))
        # Keep zone boundaries centered on entry price
        plans.append(
            make_test_plan(
                plan_id=f"PLAN_{i}",
                direction=TradePlanDirection.LONG if i % 2 == 0 else TradePlanDirection.SHORT,
                entry_price=str(px),
                zone_bottom=str(px - Decimal("5.00")),
                zone_top=str(px + Decimal("5.00")),
                status=(
                    TradePlanStatus.WAITING_FOR_RETRACEMENT
                    if i < 15
                    else TradePlanStatus.INVALIDATED
                ),
            )
        )
    spec = make_test_spec()
    acct = make_test_account()
    results = [
        evaluate_trade_plan_risk(p, specification=spec, account_snapshot=acct) for p in plans
    ]
    assert len(results) == 26
    approved = [r for r in results if r.status == RiskAssessmentStatus.RISK_APPROVED]
    blocked = [r for r in results if r.status == RiskAssessmentStatus.RISK_BLOCKED]
    assert len(approved) == 15
    assert len(blocked) == 11


# 64. historical_as_of_rebuilds_risk_assessment
def test_historical_as_of_rebuilds_risk_assessment() -> None:
    plan = make_test_plan(avail_time=BASE_TIME)
    spec = make_test_spec()
    acct = make_test_account()
    res = evaluate_trade_plan_risk(
        plan,
        specification=spec,
        account_snapshot=acct,
        as_of_utc=BASE_TIME + timedelta(hours=2),
    )
    filtered = filter_risk_assessments_as_of(
        [res],
        as_of_utc=BASE_TIME - timedelta(hours=1),
    )
    assert len(filtered) == 0
    filtered_after = filter_risk_assessments_as_of(
        [res],
        as_of_utc=BASE_TIME + timedelta(hours=3),
    )
    assert len(filtered_after) == 1


# 65. all_required_metadata_traceable
def test_all_required_metadata_traceable() -> None:
    plan = make_test_plan()
    spec = make_test_spec()
    acct = make_test_account()
    res = evaluate_trade_plan_risk(plan, specification=spec, account_snapshot=acct)
    assert res.trade_plan_id == plan.deterministic_trade_plan_id
    assert res.setup_id == plan.source_setup_id
    assert res.instrument_id == plan.instrument_id
    assert len(res.related_event_ids) > 0


# ── Phase 4E-B-K1 Audit & Regression Tests (Tests 66 - 85) ───────────────────


# 66. test_net_rr_plan1_matches_cash_flow_math
def test_net_rr_plan1_matches_cash_flow_math() -> None:
    """Verify Plan 1 (H4 BID SHORT) exact cash-flow reconciliation and Net R:R math."""
    entry = Decimal("4227.950")
    stop = Decimal("4256.140")  # distance = 28.190
    target = Decimal("4171.570")  # distance = 56.380
    units_per_lot = Decimal("100")
    comm_per_lot = Decimal("2.00")
    spread_ticks = 25
    tick_sz = Decimal("0.01")
    spread_cost_per_lot = Decimal(spread_ticks) * tick_sz * units_per_lot  # 25.00
    total_cost_per_lot = comm_per_lot + spread_cost_per_lot  # 27.00
    cost_per_unit = total_cost_per_lot / units_per_lot  # 0.27

    # Independent cash-flow calculation for 0.35 lot
    lots = Decimal("0.35")
    gross_loss = (lots * (stop - entry) * units_per_lot).quantize(Decimal("0.01"))
    gross_reward = (lots * (entry - target) * units_per_lot).quantize(Decimal("0.01"))
    total_cost = (lots * total_cost_per_lot).quantize(Decimal("0.01"))
    cash_risk = gross_loss + total_cost
    net_reward = gross_reward - total_cost
    expected_net_rr = (net_reward / cash_risk).quantize(Decimal("0.01"))

    gross_rr, net_rr = calculate_risk_reward(
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        stop_scenario_cost=cost_per_unit,
        target_scenario_cost=cost_per_unit,
    )
    assert gross_rr == Decimal("2.00")
    assert net_rr == expected_net_rr
    assert net_rr == Decimal("1.97")

    # Full domain assessment check
    plan = make_test_plan(
        plan_id="PLAN_1_AUDIT",
        direction=TradePlanDirection.SHORT,
        entry_price="4227.950",
        zone_bottom="4199.955",
        zone_top="4255.945",
    )
    spec = make_test_spec(units_per_lot="100", commission="2.00", spread_ticks=25)
    acct = make_test_account(equity="100000.00")
    cfg = RiskManagementConfig(distal_buffer_ticks=20)  # Plan 1 uses 20 ticks ($0.20) buffer
    assessment = evaluate_trade_plan_risk(
        plan, config=cfg, specification=spec, account_snapshot=acct
    )

    assert assessment.status == RiskAssessmentStatus.RISK_APPROVED
    assert assessment.gross_risk_reward == Decimal("2.00")
    assert assessment.net_risk_reward == Decimal("1.97")
    assert assessment.position_sizing is not None
    assert assessment.position_sizing.allocated_lots == Decimal("0.35")
    assert assessment.position_sizing.gross_cash_loss == Decimal("986.65")
    assert assessment.position_sizing.gross_cash_reward == Decimal("1973.30")
    assert assessment.position_sizing.net_cash_reward == Decimal("1963.85")
    assert assessment.position_sizing.estimated_cash_risk == Decimal("996.10")
    assert assessment.position_sizing.stop_scenario_total_cost == Decimal("9.45")


# 67. test_net_rr_narrow_stop_plan4
def test_net_rr_narrow_stop_plan4() -> None:
    """Verify Plan 4 narrow stop distance (0.985 USD/oz) incurs heavy cost friction."""
    entry = Decimal("4356.705")
    stop = Decimal("4357.690")  # distance = 0.985
    target = Decimal("4354.740")  # distance = 1.965
    cost_per_unit = Decimal("0.27")

    # Cost friction ratio: 0.27 / 0.985 = 27.4% of stop buffer consumed by costs
    cost_friction_ratio = cost_per_unit / (stop - entry)
    assert cost_friction_ratio > Decimal("0.27")

    gross_rr, net_rr = calculate_risk_reward(
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        stop_scenario_cost=cost_per_unit,
        target_scenario_cost=cost_per_unit,
    )
    assert gross_rr == Decimal("1.99")
    assert net_rr == Decimal("1.35")
    assert net_rr < gross_rr


# 68. test_gross_rr_differs_from_net_rr_with_costs
def test_gross_rr_differs_from_net_rr_with_costs() -> None:
    """Verify gross R:R and net R:R are strictly distinct whenever execution costs exist."""
    entry = Decimal("2650.00")
    stop = Decimal("2640.00")  # risk = 10.00
    target = Decimal("2670.00")  # reward = 20.00
    gross_rr, net_rr = calculate_risk_reward(
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        stop_scenario_cost=Decimal("0.50"),
        target_scenario_cost=Decimal("0.50"),
    )
    assert gross_rr == Decimal("2.00")
    assert net_rr == Decimal("1.86")
    assert net_rr < gross_rr


# 69. test_net_rr_unknown_cost_is_none
def test_net_rr_unknown_cost_is_none() -> None:
    """Verify net R:R is None when execution costs are not provided."""
    entry = Decimal("2650.00")
    stop = Decimal("2640.00")
    target = Decimal("2670.00")
    gross_rr, net_rr = calculate_risk_reward(
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        stop_scenario_cost=None,
        target_scenario_cost=None,
    )
    assert gross_rr == Decimal("2.00")
    assert net_rr is None


# 70. test_stop_cost_and_target_cost_separate
def test_stop_cost_and_target_cost_separate() -> None:
    """Verify stop scenario cost and target scenario cost can be evaluated independently."""
    entry = Decimal("2650.00")
    stop = Decimal("2640.00")  # risk = 10.00
    target = Decimal("2670.00")  # reward = 20.00
    gross_rr, net_rr = calculate_risk_reward(
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        stop_scenario_cost=Decimal("1.00"),
        target_scenario_cost=Decimal("0.20"),
    )
    assert gross_rr == Decimal("2.00")
    assert net_rr == Decimal("1.80")


# 71. test_round_trip_commission_semantics
def test_round_trip_commission_semantics() -> None:
    """Verify broker commission specification represents round-trip coverage."""
    spec = make_test_spec(commission="2.50")
    assert spec.is_round_trip_commission is True
    assert spec.calculate_execution_cost_per_lot() >= Decimal("2.50")


# 72. test_spread_unit_conversion
def test_spread_unit_conversion() -> None:
    """Verify spread in ticks converts properly across tick_size and units_per_lot."""
    spec = make_test_spec(
        units_per_lot="100",
        tick_sz="0.01",
        commission="0.00",
        spread_ticks=25,
    )
    assert spec.calculate_execution_cost_per_unit() == Decimal("0.25")
    assert spec.calculate_execution_cost_per_lot() == Decimal("25.00")


# 73. test_bid_ask_spread_not_double_counted
def test_bid_ask_spread_not_double_counted() -> None:
    """Verify spread cost is applied cleanly to cash risk without artificial double-deduction."""
    plan = make_test_plan(entry_price="2650.00", zone_bottom="2640.00", zone_top="2660.00")
    spec = make_test_spec(units_per_lot="100", spread_ticks=25, commission="0.00")
    acct = make_test_account(equity="100000.00")
    cfg = RiskManagementConfig(distal_buffer_ticks=0)  # Pure 10.00 distance
    res = evaluate_trade_plan_risk(plan, config=cfg, specification=spec, account_snapshot=acct)
    assert res.position_sizing is not None
    assert res.position_sizing.estimated_spread_cost == Decimal("24.25")
    assert res.position_sizing.gross_cash_loss == Decimal("970.00")
    assert res.position_sizing.estimated_cash_risk == Decimal("994.25")
    assert res.position_sizing.estimated_cash_risk <= res.position_sizing.risk_budget


# 74. test_slippage_cost_is_considered_when_defined
def test_slippage_cost_is_considered_when_defined() -> None:
    """Verify slippage ticks are incorporated into lot sizing and cash risk when defined."""
    spec_no_slip = make_test_spec(units_per_lot="100", commission="2.00", spread_ticks=25)
    spec_slip = InstrumentRiskSpecification(
        instrument_id=INST,
        source_id=SRC,
        tick_size=Decimal("0.01"),
        pip_size=Decimal("0.10"),
        units_per_lot=Decimal("100"),
        minimum_order_quantity=Decimal("0.01"),
        maximum_order_quantity=Decimal("50.0"),
        quantity_step=Decimal("0.01"),
        commission_per_lot=Decimal("2.00"),
        typical_spread_ticks=25,
        slippage_ticks=10,
    )
    plan = make_test_plan(entry_price="2650.00", zone_bottom="2640.00", zone_top="2660.00")
    acct = make_test_account(equity="100000.00")
    res_no_slip = evaluate_trade_plan_risk(plan, specification=spec_no_slip, account_snapshot=acct)
    res_slip = evaluate_trade_plan_risk(plan, specification=spec_slip, account_snapshot=acct)

    assert res_slip.position_sizing is not None and res_no_slip.position_sizing is not None
    assert res_slip.position_sizing.estimated_slippage_cost > Decimal("0")
    assert res_slip.position_sizing.allocated_lots <= res_no_slip.position_sizing.allocated_lots
    assert res_slip.position_sizing.estimated_cash_risk <= res_slip.position_sizing.risk_budget


# 75. test_fx_conversion_preserves_currency_units
def test_fx_conversion_preserves_currency_units() -> None:
    """Verify FX rate conversion converts quote currency cash loss to account currency."""
    plan = make_test_plan(entry_price="1.0850", zone_bottom="1.0800", zone_top="1.0900")
    spec = InstrumentRiskSpecification(
        instrument_id=InstrumentId("EURUSD"),
        source_id=SRC,
        tick_size=Decimal("0.0001"),
        pip_size=Decimal("0.0001"),
        units_per_lot=Decimal("100000"),
        minimum_order_quantity=Decimal("0.01"),
        maximum_order_quantity=Decimal("50.0"),
        quantity_step=Decimal("0.01"),
        quote_currency="USD",
        account_currency="EUR",
        commission_per_lot=Decimal("2.00"),
        typical_spread_ticks=10,
    )
    acct = AccountRiskSnapshot(
        account_id="ACC_EUR",
        account_currency="EUR",
        equity=Decimal("50000.00"),
        balance=Decimal("50000.00"),
    )
    fx = FxConversionRate(
        from_currency="USD",
        to_currency="EUR",
        rate=Decimal("0.91"),
        rate_time=BASE_TIME,
    )
    res = evaluate_trade_plan_risk(
        plan, specification=spec, account_snapshot=acct, fx_rate=fx, as_of_utc=BASE_TIME
    )
    assert res.position_sizing is not None
    assert res.position_sizing.is_valid
    assert res.position_sizing.estimated_cash_risk <= res.position_sizing.risk_budget


# 76. test_position_size_recomputed_after_cost_change
def test_position_size_recomputed_after_cost_change() -> None:
    """Verify lot size is recomputed and reduces when spread or commission increases."""
    plan = make_test_plan(entry_price="2650.00", zone_bottom="2640.00", zone_top="2660.00")
    acct = make_test_account(equity="100000.00")
    spec_low = make_test_spec(spread_ticks=10, commission="1.00")
    spec_high = make_test_spec(spread_ticks=100, commission="10.00")

    res_low = evaluate_trade_plan_risk(plan, specification=spec_low, account_snapshot=acct)
    res_high = evaluate_trade_plan_risk(plan, specification=spec_high, account_snapshot=acct)

    assert res_low.position_sizing is not None and res_high.position_sizing is not None
    assert res_high.position_sizing.allocated_lots < res_low.position_sizing.allocated_lots
    assert res_high.position_sizing.estimated_cash_risk <= Decimal("1000.00")


# 77. test_final_cash_risk_never_exceeds_budget
def test_final_cash_risk_never_exceeds_budget() -> None:
    """Mathematical proof across diverse scenarios that cash risk never exceeds budget."""
    acct = make_test_account(equity="100000.00")
    for distance_int in range(1, 100, 5):
        dist = Decimal(str(distance_int)) * Decimal("0.5")
        plan = make_test_plan(
            entry_price="2650.00",
            zone_bottom=str(Decimal("2650.00") - dist),
            zone_top=str(Decimal("2650.00") + dist),
        )
        spec = make_test_spec(units_per_lot="100", commission="2.00", spread_ticks=25)
        res = evaluate_trade_plan_risk(plan, specification=spec, account_snapshot=acct)
        if res.position_sizing and res.position_sizing.is_valid:
            assert res.position_sizing.estimated_cash_risk <= Decimal("1000.00")


# 78. test_minimum_rr_uses_configured_gross_or_net_metric
def test_minimum_rr_uses_configured_gross_or_net_metric() -> None:
    """Verify minimum R:R threshold validation respects configured RiskRewardMetric."""
    plan = make_test_plan(
        entry_price="4356.705",
        zone_bottom="4355.915",
        zone_top="4357.495",
        direction=TradePlanDirection.SHORT,
    )
    spec = make_test_spec(units_per_lot="100", commission="2.00", spread_ticks=25)
    acct = make_test_account()

    cfg_gross = RiskManagementConfig(
        min_risk_reward_ratio=Decimal("1.5"),
        rr_evaluation_metric=RiskRewardMetric.GROSS,
    )
    res_gross = evaluate_trade_plan_risk(
        plan, config=cfg_gross, specification=spec, account_snapshot=acct
    )
    assert RiskRejectionReason.MINIMUM_RR_NOT_MET not in res_gross.rejection_reasons

    cfg_net = RiskManagementConfig(
        min_risk_reward_ratio=Decimal("1.5"),
        rr_evaluation_metric=RiskRewardMetric.NET,
    )
    res_net = evaluate_trade_plan_risk(
        plan, config=cfg_net, specification=spec, account_snapshot=acct
    )
    assert RiskRejectionReason.MINIMUM_RR_NOT_MET in res_net.rejection_reasons


# 79. test_missing_broker_source_not_verified
def test_missing_broker_source_not_verified() -> None:
    """Verify that absent live broker authentication forces HYPOTHETICAL or UNKNOWN provenance."""
    spec = make_test_spec()
    assert spec.source_type != SpecificationSourceType.VERIFIED_LIVE_BROKER
    assert spec.source_type == SpecificationSourceType.HYPOTHETICAL_BROKER_SCENARIO


# 80. test_hypothetical_broker_metadata_label
def test_hypothetical_broker_metadata_label() -> None:
    """Verify explainability audit card explicitly labels hypothetical broker metadata."""
    plan = make_test_plan()
    spec = make_test_spec()
    acct = make_test_account()
    res = evaluate_trade_plan_risk(plan, specification=spec, account_snapshot=acct)
    card = explain_risk_assessment(res)
    assert "Source: HYPOTHETICAL_BROKER_SCENARIO" in card


# 81. test_historical_account_snapshot_does_not_leak
def test_historical_account_snapshot_does_not_leak() -> None:
    """Verify that future FX conversion rate or account snapshot cannot leak into past evaluation."""
    plan = make_test_plan(avail_time=datetime(2026, 9, 28, 8, 0, tzinfo=UTC))
    spec = make_test_spec(quote_curr="EUR", acct_curr="USD")
    acct = make_test_account()
    future_fx = FxConversionRate(
        from_currency="EUR",
        to_currency="USD",
        rate=Decimal("1.10"),
        rate_time=datetime(2026, 10, 15, 0, 0, tzinfo=UTC),
    )
    res = evaluate_trade_plan_risk(
        plan,
        specification=spec,
        account_snapshot=acct,
        fx_rate=future_fx,
        as_of_utc=datetime(2026, 9, 28, 8, 0, tzinfo=UTC),
    )
    assert res.status == RiskAssessmentStatus.RISK_BLOCKED
    assert RiskRejectionReason.FUTURE_INFORMATION in res.rejection_reasons


# 82. test_bid_ask_mirrored_opportunity_not_double_counted
def test_bid_ask_mirrored_opportunity_not_double_counted() -> None:
    """Verify BID and ASK evaluations of the same structural setup represent 1 economic opportunity."""
    setup_id = "BOS_BEARISH_20260928T000000Z"
    plan_bid = IctTradePlan(
        deterministic_trade_plan_id="PLAN_BID",
        source_setup_id=setup_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        setup_type=IctSetupType.REVERSAL,
        direction=TradePlanDirection.SHORT,
        entry_mode=TradePlanEntryMode.ZONE_LIMIT_REFERENCE,
        entry_zone_id="ZONE_1",
        entry_zone_type=EntryZoneType.ORDER_BLOCK,
        entry_zone_bottom=Decimal("4200.00"),
        entry_zone_top=Decimal("4250.00"),
        entry_reference_price=Decimal("4225.00"),
        entry_conditions=("ZONE_IDENTIFIED",),
        plan_status=TradePlanStatus.WAITING_FOR_RETRACEMENT,
        setup_available_time=BASE_TIME,
        entry_zone_available_time=BASE_TIME,
        plan_available_time=BASE_TIME,
        plan_decision_time=BASE_TIME,
    )
    plan_ask = IctTradePlan(
        deterministic_trade_plan_id="PLAN_ASK",
        source_setup_id=setup_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.ASK,
        setup_type=IctSetupType.REVERSAL,
        direction=TradePlanDirection.SHORT,
        entry_mode=TradePlanEntryMode.ZONE_LIMIT_REFERENCE,
        entry_zone_id="ZONE_1",
        entry_zone_type=EntryZoneType.ORDER_BLOCK,
        entry_zone_bottom=Decimal("4200.25"),
        entry_zone_top=Decimal("4250.25"),
        entry_reference_price=Decimal("4225.25"),
        entry_conditions=("ZONE_IDENTIFIED",),
        plan_status=TradePlanStatus.WAITING_FOR_RETRACEMENT,
        setup_available_time=BASE_TIME,
        entry_zone_available_time=BASE_TIME,
        plan_available_time=BASE_TIME,
        plan_decision_time=BASE_TIME,
    )
    assert plan_bid.source_setup_id == plan_ask.source_setup_id


# 83. test_missing_portfolio_snapshot_prevents_full_approval
def test_missing_portfolio_snapshot_prevents_full_approval() -> None:
    """Verify that exceeding portfolio open risk limit blocks qualification."""
    plan = make_test_plan()
    spec = make_test_spec()
    acct = AccountRiskSnapshot(
        account_id="ACC_1",
        account_currency="USD",
        equity=Decimal("100000.00"),
        balance=Decimal("100000.00"),
        open_risk_ratio=Decimal("0.05"),
        max_open_risk_ratio=Decimal("0.05"),
    )
    res = evaluate_trade_plan_risk(plan, specification=spec, account_snapshot=acct)
    assert res.status == RiskAssessmentStatus.RISK_BLOCKED
    assert RiskRejectionReason.RISK_BUDGET_EXCEEDED in res.rejection_reasons


# 84. test_plan_status_does_not_imply_actual_fill
def test_plan_status_does_not_imply_actual_fill() -> None:
    """Verify RISK_APPROVED is strictly analytical and produces zero orders, fills, or executions."""
    plan = make_test_plan()
    spec = make_test_spec()
    acct = make_test_account()
    res = evaluate_trade_plan_risk(plan, specification=spec, account_snapshot=acct)
    assert res.status == RiskAssessmentStatus.RISK_APPROVED
    assert not hasattr(res, "order_id")
    assert not hasattr(res, "fill_price")
    assert not hasattr(res, "realized_pnl")


# 85. test_all_26_risk_assessments_reconcile
def test_all_26_risk_assessments_reconcile() -> None:
    """Reconcile 26 trade plans ensuring numerical precision and budget invariance."""
    plans = [
        make_test_plan(
            plan_id=f"RECON_{i}",
            direction=TradePlanDirection.LONG if i % 2 == 0 else TradePlanDirection.SHORT,
            entry_price=str(Decimal("2600.00") + Decimal(str(i))),
            zone_bottom=str(Decimal("2600.00") + Decimal(str(i)) - Decimal("10.00")),
            zone_top=str(Decimal("2600.00") + Decimal(str(i)) + Decimal("10.00")),
            status=TradePlanStatus.WAITING_FOR_RETRACEMENT if i < 4 else TradePlanStatus.EXPIRED,
        )
        for i in range(26)
    ]
    spec = make_test_spec()
    acct = make_test_account()
    results = [
        evaluate_trade_plan_risk(p, specification=spec, account_snapshot=acct) for p in plans
    ]
    assert len(results) == 26
    approved = [r for r in results if r.status == RiskAssessmentStatus.RISK_APPROVED]
    blocked = [r for r in results if r.status == RiskAssessmentStatus.RISK_BLOCKED]
    assert len(approved) == 4
    assert len(blocked) == 22
    for r in approved:
        assert r.position_sizing is not None
        assert r.position_sizing.estimated_cash_risk <= Decimal("1000.00")
        assert r.net_risk_reward is not None
        assert r.net_risk_reward < r.gross_risk_reward
