"""Economic opportunity identity, structural evidence, grouping, and deduplication.

This module models market opportunities independent of temporal sampling (H2 vs H4)
or quotation sides (BID vs ASK). It provides deterministic identity generation,
evidence linkage, and configurable deduplication policies.
"""

from __future__ import annotations

import enum
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from traderlab.core.types import DataSourceId, InstrumentId, PriceType, Timeframe
from traderlab.domain.risk.ict_risk_management import (
    IctRiskAssessment,
    RiskAssessmentStatus,
)
from traderlab.domain.trade_plans.ict_trade_plan import (
    IctTradePlan,
    TradePlanDirection,
    TradePlanStatus,
)


# ── Policies & Enums ────────────────────────────────────────────────────────
class OpportunityDeduplicationPolicy(enum.Enum):
    """Policy for handling multiple alternative plans representing one economic event."""

    ONE_PLAN_PER_ECONOMIC_EVENT = "ONE_PLAN_PER_ECONOMIC_EVENT"
    """Default production policy: selects at most 1 plan per economic opportunity."""

    KEEP_ALL_FOR_RESEARCH = "KEEP_ALL_FOR_RESEARCH"
    """Analytical policy: retains all plans grouped under the opportunity without independent risk."""

    GROUP_LEVEL_RISK_BUDGET = "GROUP_LEVEL_RISK_BUDGET"
    """Risk allocation policy: enforces a single risk budget across all plans in the opportunity group."""


class PlanSelectionPolicy(enum.Enum):
    """Rule used to select a single primary trade plan from an economic opportunity group."""

    EARLIEST_PIT_ELIGIBLE = "EARLIEST_PIT_ELIGIBLE"
    """Default policy: selects the earliest point-in-time actionable plan with stable tie-breaking."""

    TIMEFRAME_PRIORITY = "TIMEFRAME_PRIORITY"
    """Selects the plan matching the highest priority configured timeframe."""

    HIGHEST_VALID_NET_RR = "HIGHEST_VALID_NET_RR"
    """Selects the candidate with the highest Net Risk/Reward known at decision time."""

    EXPLICIT_PLAN_ID = "EXPLICIT_PLAN_ID"
    """Selects an explicitly user-configured deterministic plan ID if eligible."""


# ── Domain Models ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class EconomicOpportunityEvidence:
    """Immutable structural evidence linking trade plans to an underlying market event."""

    instrument_id: InstrumentId
    underlying_family: str
    direction: TradePlanDirection
    structural_trigger_type: str
    structural_trigger_time_utc: datetime
    origin_swing_date: str
    causal_event_ids: tuple[str, ...] = field(default_factory=tuple)
    explanation: str = ""


@dataclass(frozen=True, slots=True)
class EconomicOpportunity:
    """Canonical, discrete financial market opportunity derived from market structure."""

    economic_opportunity_id: str
    evidence: EconomicOpportunityEvidence
    first_observed_time_utc: datetime
    available_at_utc: datetime


@dataclass(frozen=True, slots=True)
class SelectedTradePlan:
    """Trade plan selected as the primary execution candidate for an economic opportunity."""

    plan: IctTradePlan
    assessment: IctRiskAssessment
    opportunity_id: str
    selection_policy: PlanSelectionPolicy
    selection_reason: str
    selection_time_utc: datetime


@dataclass(frozen=True, slots=True)
class RejectedTradePlan:
    """Trade plan rejected or set aside as a duplicate alternative during validation."""

    plan: IctTradePlan
    assessment: IctRiskAssessment | None
    opportunity_id: str | None
    rejection_reasons: tuple[str, ...]
    rejection_explanation: str
    evaluation_time_utc: datetime


@dataclass(frozen=True, slots=True)
class EconomicOpportunityGroup:
    """Group of co-occurring trade plans sharing the same underlying economic opportunity."""

    opportunity: EconomicOpportunity
    plans: tuple[IctTradePlan, ...]
    risk_assessments: tuple[IctRiskAssessment, ...]
    timeframes: tuple[Timeframe, ...]
    price_types: tuple[PriceType, ...]
    total_proposed_risk: Decimal
    max_single_plan_risk: Decimal


# ── Helper Parsing Functions ────────────────────────────────────────────────
def extract_underlying_family(source_id: DataSourceId | str) -> str:
    """Extract clean base source family (e.g. 'market_data_lab_m1' from derived series)."""
    src_str = str(source_id)
    if "_derived" in src_str:
        return src_str.split("_derived")[0]
    return src_str


def parse_structural_signature(setup_id: str) -> tuple[str, datetime | None, str]:
    """Parse structural trigger type, timestamp, and origin date from setup ID."""
    parts = setup_id.split(":")
    trigger_token = setup_id
    for part in reversed(parts):
        if any(tok in part for tok in ("BOS_", "CHOCH_")):
            trigger_token = part
            break

    # Pattern for BOS/CHoCH with timestamp: e.g. BOS_BEARISH_20260928T000000Z
    # and swing date e.g. SWING_LOW_20260925T120000Z
    trigger_type = "UNKNOWN_TRIGGER"
    if "BOS_BEARISH" in trigger_token:
        trigger_type = "BOS_BEARISH"
    elif "BOS_BULLISH" in trigger_token:
        trigger_type = "BOS_BULLISH"
    elif "CHOCH_BEARISH" in trigger_token:
        trigger_type = "CHOCH_BEARISH"
    elif "CHOCH_BULLISH" in trigger_token:
        trigger_type = "CHOCH_BULLISH"

    # Extract event timestamp (e.g. 20260928T000000Z)
    event_time_utc: datetime | None = None
    time_matches = re.findall(r"(\d{8}T\d{6}Z)", trigger_token)
    if time_matches:
        try:
            event_time_utc = datetime.strptime(time_matches[0], "%Y%m%dT%H%M%SZ").replace(
                tzinfo=UTC
            )
        except ValueError:
            event_time_utc = None

    # Extract origin swing anchor (e.g. SWING_LOW_20260925T120000Z)
    origin_date = "UNKNOWN_ORIGIN"
    m_swing = re.findall(
        r"(SWING_[A-Z]+)_(\d{8}T(\d{2})\d{4}Z)(?:_([A-Za-z0-9_\.]+))?", trigger_token
    )
    if m_swing:
        stype, full_ts, hh_str, suffix = m_swing[0]
        hh = int(hh_str)
        # Canonical 4-hour epoch anchor: (hh // 4) * 4 to align H2 and H4
        h4_bucket = (hh // 4) * 4
        date_part = full_ts[:8]
        # Check if suffix contains explicit price/level/distinct swing identifier
        discriminator = ""
        if suffix and suffix not in ("3L3R", "5L5R", "2L2R", "NONE"):
            discriminator = f"_{suffix}"
        origin_date = f"{stype}_{date_part}T{h4_bucket:02d}0000Z{discriminator}"
    else:
        origin_matches = re.findall(r"(SWING_[A-Z]+)_(\d{8})", trigger_token)
        if origin_matches:
            origin_date = f"{origin_matches[0][0]}_{origin_matches[0][1]}"
        elif len(time_matches) > 1:
            origin_date = time_matches[1][:8]

    return trigger_type, event_time_utc, origin_date


def generate_economic_opportunity_id(
    instrument_id: InstrumentId | str,
    underlying_family: str,
    direction: TradePlanDirection,
    trigger_type: str,
    trigger_time_utc: datetime,
    origin_date: str,
) -> str:
    """Generate deterministic, canonical Economic Opportunity ID."""
    iso_time = trigger_time_utc.strftime("%Y%m%dT%H%M%SZ")
    raw_sig = (
        f"{instrument_id!s}:{underlying_family}:{direction.value}:{trigger_type}:"
        f"{iso_time}:{origin_date}"
    )
    digest = hashlib.sha256(raw_sig.encode("utf-8")).hexdigest()[:12]
    return f"OPP_{instrument_id!s}_{direction.value}_{trigger_type}_{iso_time}_{digest}"


def identify_economic_opportunity(
    plan: IctTradePlan,
    assessment: IctRiskAssessment | None = None,
) -> EconomicOpportunity:
    """Extract immutable structural evidence and determine canonical Economic Opportunity."""
    family = extract_underlying_family(plan.source_id)
    trigger_type, trigger_time, origin_date = parse_structural_signature(plan.source_setup_id)

    event_time = trigger_time or plan.setup_available_time
    opp_id = generate_economic_opportunity_id(
        instrument_id=plan.instrument_id,
        underlying_family=family,
        direction=plan.direction,
        trigger_type=trigger_type,
        trigger_time_utc=event_time,
        origin_date=origin_date,
    )

    causal_ids = tuple(plan.entry_conditions)
    if assessment and assessment.related_event_ids:
        causal_ids = assessment.related_event_ids

    evidence = EconomicOpportunityEvidence(
        instrument_id=plan.instrument_id,
        underlying_family=family,
        direction=plan.direction,
        structural_trigger_type=trigger_type,
        structural_trigger_time_utc=event_time,
        origin_swing_date=origin_date,
        causal_event_ids=causal_ids,
        explanation=(
            f"Opportunity triggered by {trigger_type} at {event_time.strftime('%Y-%m-%d %H:%M UTC')} "
            f"originating from swing level {origin_date} across {family}"
        ),
    )

    return EconomicOpportunity(
        economic_opportunity_id=opp_id,
        evidence=evidence,
        first_observed_time_utc=plan.setup_available_time,
        available_at_utc=plan.plan_available_time,
    )


def group_trade_plans_by_opportunity(
    plans: Sequence[IctTradePlan],
    assessments: Sequence[IctRiskAssessment] | None = None,
) -> tuple[EconomicOpportunityGroup, ...]:
    """Deterministically group trade plans by their canonical Economic Opportunity."""
    assessments_by_plan_id: dict[str, IctRiskAssessment] = {}
    if assessments:
        for a in assessments:
            assessments_by_plan_id[a.trade_plan_id] = a

    groups_dict: dict[str, dict[str, Any]] = {}

    for plan in plans:
        ass = assessments_by_plan_id.get(plan.deterministic_trade_plan_id)
        opp = identify_economic_opportunity(plan, ass)

        if opp.economic_opportunity_id not in groups_dict:
            groups_dict[opp.economic_opportunity_id] = {
                "opportunity": opp,
                "plans": [],
                "assessments": [],
            }

        groups_dict[opp.economic_opportunity_id]["plans"].append(plan)
        if ass:
            groups_dict[opp.economic_opportunity_id]["assessments"].append(ass)

    # Convert to frozen deterministic groups
    result: list[EconomicOpportunityGroup] = []
    # Sort group keys for deterministic output regardless of input order
    for opp_id in sorted(groups_dict.keys()):
        g = groups_dict[opp_id]
        grp_plans = tuple(g["plans"])
        grp_ass = tuple(g["assessments"])

        tfs = tuple(sorted(set(p.timeframe for p in grp_plans), key=lambda t: t.name))
        pts = tuple(sorted(set(p.price_type for p in grp_plans), key=lambda p: p.value))

        # Calculate risk totals
        total_risk = Decimal("0.00")
        max_risk = Decimal("0.00")
        for a in grp_ass:
            if a.position_sizing and a.position_sizing.is_valid:
                risk = a.position_sizing.estimated_cash_risk
                total_risk += risk
                if risk > max_risk:
                    max_risk = risk

        result.append(
            EconomicOpportunityGroup(
                opportunity=g["opportunity"],
                plans=grp_plans,
                risk_assessments=grp_ass,
                timeframes=tfs,
                price_types=pts,
                total_proposed_risk=total_risk,
                max_single_plan_risk=max_risk,
            )
        )

    return tuple(result)


def select_plan_per_opportunity(
    group: EconomicOpportunityGroup,
    *,
    policy: PlanSelectionPolicy = PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE,
    timeframe_priority: Sequence[Timeframe] = (Timeframe.H4, Timeframe.H2, Timeframe.D1),
    explicit_plan_id: str | None = None,
    as_of_utc: datetime | None = None,
) -> tuple[SelectedTradePlan | None, tuple[RejectedTradePlan, ...]]:
    """Select at most 1 primary trade plan from an economic group using a deterministic rule."""
    cutoff = (
        as_of_utc.replace(tzinfo=UTC)
        if as_of_utc is not None
        else datetime.max.replace(tzinfo=UTC)
    )

    # 1. Filter candidates available at or before cutoff
    candidates: list[tuple[IctTradePlan, IctRiskAssessment]] = []
    assessments_by_plan = {a.trade_plan_id: a for a in group.risk_assessments}

    for p in group.plans:
        if p.plan_available_time <= cutoff:
            ass = assessments_by_plan.get(p.deterministic_trade_plan_id)
            if ass:
                candidates.append((p, ass))

    if not candidates:
        return None, ()

    # 2. Filter strictly active / approved plans first
    active_candidates: list[tuple[IctTradePlan, IctRiskAssessment]] = []
    for p, a in candidates:
        if a.status == RiskAssessmentStatus.RISK_APPROVED and p.plan_status in (
            TradePlanStatus.WAITING_FOR_RETRACEMENT,
            TradePlanStatus.TOUCH_OBSERVED,
            TradePlanStatus.REACTION_CONFIRMED,
        ):
            active_candidates.append((p, a))

    # If no active candidates exist, do not select any plan
    if not active_candidates:
        rejected: list[RejectedTradePlan] = []
        for p, a in candidates:
            rejected.append(
                RejectedTradePlan(
                    plan=p,
                    assessment=a,
                    opportunity_id=group.opportunity.economic_opportunity_id,
                    rejection_reasons=("PLAN_NOT_ACTIVE",),
                    rejection_explanation="Trade plan is not active or risk assessment was blocked",
                    evaluation_time_utc=cutoff,
                )
            )
        return None, tuple(rejected)

    # 3. Apply Selection Policy on active candidates
    selected_pair: tuple[IctTradePlan, IctRiskAssessment] | None = None
    selection_reason = ""

    if policy == PlanSelectionPolicy.EXPLICIT_PLAN_ID:
        if explicit_plan_id:
            for p, a in active_candidates:
                if p.deterministic_trade_plan_id == explicit_plan_id:
                    selected_pair = (p, a)
                    selection_reason = (
                        f"Explicitly configured plan ID selected: {explicit_plan_id}"
                    )
                    break
        if not selected_pair:
            # Explicit plan not found or not eligible
            rejected_explicit = tuple(
                RejectedTradePlan(
                    plan=p,
                    assessment=a,
                    opportunity_id=group.opportunity.economic_opportunity_id,
                    rejection_reasons=("EXPLICIT_PLAN_NOT_ELIGIBLE",),
                    rejection_explanation=f"Explicit plan {explicit_plan_id} was not among eligible candidates",
                    evaluation_time_utc=cutoff,
                )
                for p, a in active_candidates
            )
            return None, rejected_explicit

    elif policy == PlanSelectionPolicy.TIMEFRAME_PRIORITY:
        # Sort by timeframe priority, then earliest time, then stable plan ID
        tf_rank = {tf: idx for idx, tf in enumerate(timeframe_priority)}
        sorted_candidates = sorted(
            active_candidates,
            key=lambda item: (
                tf_rank.get(item[0].timeframe, 999),
                item[0].plan_available_time,
                item[0].deterministic_trade_plan_id,
            ),
        )
        selected_pair = sorted_candidates[0]
        selection_reason = f"Selected by timeframe priority ({selected_pair[0].timeframe.name})"

    elif policy == PlanSelectionPolicy.HIGHEST_VALID_NET_RR:
        # Sort by Net R:R descending, then earliest time, then stable plan ID
        sorted_candidates = sorted(
            active_candidates,
            key=lambda item: (
                -(
                    item[1].net_risk_reward
                    if item[1].net_risk_reward is not None
                    else Decimal("0.0")
                ),
                item[0].plan_available_time,
                item[0].deterministic_trade_plan_id,
            ),
        )
        selected_pair = sorted_candidates[0]
        net_rr = selected_pair[1].net_risk_reward
        selection_reason = f"Selected by highest valid Net R:R ({net_rr}R)"

    else:  # EARLIEST_PIT_ELIGIBLE (default)
        tf_rank = {tf: idx for idx, tf in enumerate(timeframe_priority)}
        sorted_candidates = sorted(
            active_candidates,
            key=lambda item: (
                item[0].plan_available_time,
                tf_rank.get(item[0].timeframe, 999),
                item[0].deterministic_trade_plan_id,
            ),
        )
        selected_pair = sorted_candidates[0]
        avail = selected_pair[0].plan_available_time.strftime("%Y-%m-%d %H:%M UTC")
        selection_reason = f"Selected as earliest PIT eligible candidate available at {avail}"

    assert selected_pair is not None
    selected_plan, selected_ass = selected_pair

    # Form SelectedTradePlan and RejectedTradePlan tuple for duplicates
    # Selection time reflects the historical point-in-time when the plan became available & eligible
    primary = SelectedTradePlan(
        plan=selected_plan,
        assessment=selected_ass,
        opportunity_id=group.opportunity.economic_opportunity_id,
        selection_policy=policy,
        selection_reason=selection_reason,
        selection_time_utc=selected_plan.plan_available_time,
    )

    rejected_list: list[RejectedTradePlan] = []
    for p, a in candidates:
        if p.deterministic_trade_plan_id != selected_plan.deterministic_trade_plan_id:
            reason = (
                "DUPLICATE_ECONOMIC_OPPORTUNITY"
                if a.status == RiskAssessmentStatus.RISK_APPROVED
                else "PLAN_NOT_ACTIVE"
            )
            rejected_list.append(
                RejectedTradePlan(
                    plan=p,
                    assessment=a,
                    opportunity_id=group.opportunity.economic_opportunity_id,
                    rejection_reasons=(reason,),
                    rejection_explanation=(
                        f"Set aside as duplicate alternative; primary plan "
                        f"{selected_plan.deterministic_trade_plan_id} selected under {policy.value}"
                    ),
                    evaluation_time_utc=cutoff,
                )
            )

    return primary, tuple(rejected_list)
