# Phase 4E-B-K1: Net Risk/Reward, Execution Cost, Position Sizing & Broker Metadata Integrity Audit Report

**Status:** ACCEPTED — RISK MATHEMATICS & COST INTEGRITY VERIFIED  
**Date:** 2026-10-31  
**Project:** TraderLab (`https://github.com/ferhat-yldz/traderLab`)  
**Scope:** Analytical Risk Management Domain (`src/traderlab/domain/risk/`)  
**Auditor:** Principal Quantitative Systems Architect, Financial Risk Mathematics Auditor, Point-in-Time Integrity Auditor  
**Baseline Test Count:** 893 passing  
**Final Test Count:** 913 passing (20 new mandatory audit & regression tests)  
**Quality Gates:** 0 ruff errors, 112 formatted files, 73 source files clean in `mypy`  

---

## 1. Executive Summary

Following the completion of Phase 4E-B, an exhaustive quantitative audit was conducted on the mathematical integrity of the ICT Risk Management Engine. The audit focused on the numerical coherence between Gross Risk/Reward ($R_{\text{gross}}$), Net Risk/Reward ($R_{\text{net}}$), execution costs (spread, round-trip commission, slippage), cash risk allocation, and point-in-time (PIT) safety across all 26 real historical trade plans generated from XAUUSD market data (September 15 – October 15, 2026).

### Key Audit Findings & Reconciliation:
1. **Numerical Discrepancy Resolved:** In Phase 4E-B, Plan 1 reported a Net R:R of `2.00R` (identical to Gross R:R) while actual cash flows yielded `1.97R`. Plan 4 reported `1.94R` while actual cash flows yielded `1.35R`. The discrepancy occurred because `evaluate_trade_plan_risk` previously only deducted commission ($0.02/oz) in `calculate_risk_reward`, omitting the 25-tick spread ($0.25/oz) and slippage, whereas `calculate_position_size` accounted for both. The cost model has now been unified: Net R:R is derived from the exact same cash flows as position sizing:
   $$\text{Net R:R} = \frac{\text{Gross Cash Reward} - \text{Target Scenario Cost}}{\text{Gross Cash Loss} + \text{Stop Scenario Cost}}$$
2. **Narrow Stop Cost Friction Discovered (Plan 4):** Plan 4 possesses a narrow stop distance of $0.985$ USD/oz. Total execution costs of $0.27$ USD/oz consume $27.4\%$ of the entire stop-loss risk buffer. Consequently, while Gross R:R is $1.99R$, Net R:R drops to $1.35R$. This proves the critical necessity of evaluating cost-adjusted Net R:R for short-distance trades.
3. **Plan Classification Reconciliation (4 Approved / 22 Blocked = 26 Total):** All 26 plans generated from real XAUUSD market data reconcile with 100% mathematical precision. Exactly 4 plans achieve `RISK_APPROVED` status (`WAITING_FOR_RETRACEMENT`), and exactly 22 plans achieve `RISK_BLOCKED` status (`PLAN_NOT_ACTIVE` due to lifecycle invalidation or expiration).
4. **Economic Opportunity Overlap:** The 4 `RISK_APPROVED` plans (Plan 1 H4 BID, Plan 7 H2 BID, Plan 14 H4 ASK, Plan 20 H2 ASK) all stem from the exact same underlying market structure event: a Bearish BOS/Displacement at 2026-09-28 00:00 UTC and FVG at 2026-09-28 08:00 UTC. They represent 1 single economic event, totaling $\$3,983.65$ in correlated risk. The audit confirms that portfolio risk limits must treat these as a single economic allocation.
5. **Broker Specification Provenance:** In the absence of live broker API credentials, contract metadata is strictly categorized as `SpecificationSourceType.HYPOTHETICAL_BROKER_SCENARIO` (never `VERIFIED_LIVE_BROKER`).

---

## 2. Root Cause Analysis of Mathematical Discrepancies

### The Discrepancy
In Phase 4E-B:
- **Plan 1:** Reported Gross R:R was `2.00R`, reported Net R:R was `2.00R`. Actual cash-flow math indicated Net R:R should be `1.97R`.
- **Plan 4:** Reported Gross R:R was `2.00R`, reported Net R:R was `1.94R`. Actual cash-flow math indicated Net R:R should be `1.35R`.

### Root Cause
In Phase 4E-B `evaluate_trade_plan_risk`:
```python
# PREVIOUS FLAWED IMPLEMENTATION:
trading_cost = None
if specification and specification.commission_per_lot is not None:
    trading_cost = specification.commission_per_lot / specification.units_per_lot
```
For XAUUSD:
- Commission was $\$2.00 / 100\text{ oz} = \$0.02/\text{oz}$.
- Typical spread was 25 ticks $\times \$0.01 \times 100\text{ oz} = \$25.00/\text{lot} = \$0.25/\text{oz}$.
- `calculate_risk_reward` was passed only `trading_cost = 0.02`, ignoring spread ($0.25$) and slippage ($0.00$).
- Meanwhile, `calculate_position_size` was computing:
  $$\text{total\_risk\_per\_lot} = \text{price\_risk} + \$2.00\text{ (comm)} + \$25.00\text{ (spread)} = \text{price\_risk} + \$27.00$$
This caused position sizing to account for $\$0.27$/oz, while Net R:R accounted for only $\$0.02$/oz!

### The Solution: Unified Execution Cost Architecture
We introduced `calculate_execution_cost_per_lot()` and `calculate_execution_cost_per_unit()` directly on `InstrumentRiskSpecification`:
$$\text{Cost per lot} = \text{Commission} + (\text{Spread Ticks} \times \text{Tick Size} \times \text{Units per Lot}) + (\text{Slippage Ticks} \times \text{Tick Size} \times \text{Units per Lot})$$
$$\text{Cost per unit} = \frac{\text{Cost per lot}}{\text{Units per lot}}$$

Now `evaluate_trade_plan_risk` passes the exact unified unit cost ($0.27$/oz) as `stop_scenario_cost` and `target_scenario_cost` into `calculate_risk_reward`. Furthermore, `PositionSizingResult` explicitly tracks `gross_cash_loss`, `gross_cash_reward`, `net_cash_reward`, and total scenario costs.

---

## 3. Cost Unit Deconstruction

To eliminate dimensional ambiguity across financial calculations, all risk variables are mapped to explicit physical and monetary units:

| Dimension / Unit | Physical Meaning | XAUUSD Standard Value | Internal Representation |
| :--- | :--- | :--- | :--- |
| **Price Units** | Quotation price in quote currency | Dollars per troy ounce (USD/oz) | `Decimal("4227.950")` |
| **Ticks** | Minimum price increment | $0.01$ USD/oz | `Decimal("0.01")` |
| **Contract Unit** | Physical delivery volume per unit | 1 troy ounce | 1 unit |
| **Standard Lot** | Standard contract volume | 100 troy ounces | `Decimal("100")` |
| **Commission** | Broker round-trip execution fee | $\$2.00$ per standard lot | `Decimal("2.00")` |
| **Commission / Unit** | Round-trip commission per troy ounce | $\$2.00 / 100 = \$0.02$ per oz | `Decimal("0.02")` |
| **Spread in Ticks** | Typical bid-ask distance in ticks | 25 ticks | `25` (int) |
| **Spread Price Width** | Typical bid-ask distance in price | $25 \times 0.01 = \$0.25$ per oz | `Decimal("0.25")` |
| **Spread Cost / Lot** | Spread friction on 1.0 standard lot | $\$0.25 \times 100 = \$25.00$ | `Decimal("25.00")` |
| **Total Cost / Lot** | Commission + Spread + Slippage | $\$2.00 + \$25.00 + \$0.00 = \$27.00$ | `Decimal("27.00")` |
| **Total Cost / Unit** | Execution friction per ounce | $\$27.00 / 100 = \$0.27$ per oz | `Decimal("0.27")` |
| **Account Currency** | Base accounting currency | USD | `str = "USD"` |

### Commission Semantics:
The specification field `is_round_trip_commission: bool = True` explicitly certifies that `commission_per_lot` represents the complete round-trip fee (covering both position entry and exit).

---

## 4. Complete 26-Plan Numerical Reconciliation Table

The table below reconciles all 26 trade plans generated across 8 series on real Dukascopy-derived XAUUSD tick data (September 15 – October 15, 2026), with account equity of $\$100,000.00$, risk fraction of $1.0\%$ ($\$1,000.00$ budget), 100 units/lot, 25 spread ticks, and $\$2.00$ round-trip commission:

| Plan | Series | Dir | Entry | Stop | Target | Stop Dist | Tgt Dist | Gross R:R | Lot | Gross Loss | Gross Reward | Spread Cost | Comm Cost | Stop Cost | Target Cost | Cash Risk | Net Reward | Net R:R | Budget | Status | Rejection Reason |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Plan 1 | H4 BID | SHORT | 4227.950 | 4256.14 | 4171.57 | 28.190 | 56.380 | 2.00R | 0.35 | $986.65 | $1973.30 | $8.75 | $0.70 | $9.45 | $9.45 | $996.10 | $1963.85 | 1.97R | $1000.00 | RISK_APPROVED | NONE |
| Plan 2 | H4 BID | SHORT | 4121.0165 | 4135.86 | 4091.33 | 14.8435 | 29.6865 | 2.00R | 0.66 | $979.67 | $1959.31 | $16.50 | $1.32 | $17.82 | $17.82 | $997.49 | $1941.49 | 1.95R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 3 | H2 BID | LONG | 4368.055 | 4361.34 | 4381.48 | 6.715 | 13.425 | 2.00R | 1.43 | $960.24 | $1919.78 | $35.75 | $2.86 | $38.61 | $38.61 | $998.86 | $1881.17 | 1.88R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 4 | H2 BID | SHORT | 4356.705 | 4357.69 | 4354.74 | 0.985 | 1.965 | 1.99R | 7.96 | $784.06 | $1564.14 | $199.00 | $15.92 | $214.92 | $214.92 | $998.98 | $1349.22 | 1.35R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 5 | H2 BID | SHORT | 4331.695 | 4339.45 | 4316.18 | 7.755 | 15.515 | 2.00R | 1.24 | $961.62 | $1923.86 | $31.00 | $2.48 | $33.48 | $33.48 | $995.10 | $1890.38 | 1.90R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 6 | H2 BID | SHORT | 4270.800 | 4274.06 | 4264.28 | 3.260 | 6.520 | 2.00R | 2.83 | $922.58 | $1845.16 | $70.75 | $5.66 | $76.41 | $76.41 | $998.99 | $1768.75 | 1.77R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 7 | H2 BID | SHORT | 4236.585 | 4256.14 | 4197.48 | 19.555 | 39.105 | 2.00R | 0.50 | $977.75 | $1955.25 | $12.50 | $1.00 | $13.50 | $13.50 | $991.25 | $1941.75 | 1.96R | $1000.00 | RISK_APPROVED | NONE |
| Plan 8 | H2 BID | SHORT | 4169.645 | 4176.52 | 4155.90 | 6.875 | 13.745 | 2.00R | 1.39 | $955.62 | $1910.56 | $34.75 | $2.78 | $37.53 | $37.53 | $993.16 | $1873.03 | 1.89R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 9 | H2 BID | SHORT | 4158.740 | 4174.66 | 4126.90 | 15.920 | 31.840 | 2.00R | 0.61 | $971.12 | $1942.24 | $15.25 | $1.22 | $16.47 | $16.47 | $987.59 | $1925.77 | 1.95R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 10 | H2 BID | LONG | 4157.170 | 4144.62 | 4182.27 | 12.550 | 25.100 | 2.00R | 0.78 | $978.90 | $1957.80 | $19.50 | $1.56 | $21.06 | $21.06 | $999.96 | $1936.74 | 1.94R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 11 | H2 BID | LONG | 4157.170 | 4144.62 | 4182.27 | 12.550 | 25.100 | 2.00R | 0.78 | $978.90 | $1957.80 | $19.50 | $1.56 | $21.06 | $21.06 | $999.96 | $1936.74 | 1.94R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 12 | H2 BID | LONG | 4200.185 | 4198.93 | 4202.70 | 1.255 | 2.515 | 2.00R | 6.55 | $822.02 | $1647.32 | $163.75 | $13.10 | $176.85 | $176.85 | $998.88 | $1470.47 | 1.47R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 13 | H2 BID | SHORT | 4120.715 | 4135.86 | 4090.42 | 15.145 | 30.295 | 2.00R | 0.64 | $969.28 | $1938.88 | $16.00 | $1.28 | $17.28 | $17.28 | $986.56 | $1921.60 | 1.95R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 14 | H4 ASK | SHORT | 4229.660 | 4258.74 | 4171.50 | 29.080 | 58.160 | 2.00R | 0.34 | $988.72 | $1977.44 | $8.50 | $0.68 | $9.18 | $9.18 | $997.90 | $1968.26 | 1.97R | $1000.00 | RISK_APPROVED | NONE |
| Plan 15 | H4 ASK | SHORT | 4121.495 | 4136.35 | 4091.78 | 14.855 | 29.715 | 2.00R | 0.66 | $980.43 | $1961.19 | $16.50 | $1.32 | $17.82 | $17.82 | $998.25 | $1943.37 | 1.95R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 16 | H2 ASK | LONG | 4368.740 | 4362.00 | 4382.22 | 6.740 | 13.480 | 2.00R | 1.42 | $957.08 | $1914.16 | $35.50 | $2.84 | $38.34 | $38.34 | $995.42 | $1875.82 | 1.88R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 17 | H2 ASK | SHORT | 4357.400 | 4358.52 | 4355.16 | 1.120 | 2.240 | 2.00R | 7.19 | $805.28 | $1610.56 | $179.75 | $14.38 | $194.13 | $194.13 | $999.41 | $1416.43 | 1.42R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 18 | H2 ASK | SHORT | 4332.425 | 4340.21 | 4316.86 | 7.785 | 15.565 | 2.00R | 1.24 | $965.34 | $1930.06 | $31.00 | $2.48 | $33.48 | $33.48 | $998.82 | $1896.58 | 1.90R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 19 | H2 ASK | SHORT | 4271.510 | 4274.92 | 4264.69 | 3.410 | 6.820 | 2.00R | 2.71 | $924.11 | $1848.22 | $67.75 | $5.42 | $73.17 | $73.17 | $997.28 | $1775.05 | 1.78R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 20 | H2 ASK | SHORT | 4238.210 | 4258.74 | 4197.15 | 20.530 | 41.060 | 2.00R | 0.48 | $985.44 | $1970.88 | $12.00 | $0.96 | $12.96 | $12.96 | $998.40 | $1957.92 | 1.96R | $1000.00 | RISK_APPROVED | NONE |
| Plan 21 | H2 ASK | SHORT | 4170.285 | 4177.23 | 4156.40 | 6.945 | 13.885 | 2.00R | 1.38 | $958.41 | $1916.13 | $34.50 | $2.76 | $37.26 | $37.26 | $995.67 | $1878.87 | 1.89R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 22 | H2 ASK | SHORT | 4159.290 | 4175.20 | 4127.47 | 15.910 | 31.820 | 2.00R | 0.61 | $970.51 | $1941.02 | $15.25 | $1.22 | $16.47 | $16.47 | $986.98 | $1924.55 | 1.95R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 23 | H2 ASK | LONG | 4157.740 | 4145.33 | 4182.56 | 12.410 | 24.820 | 2.00R | 0.78 | $967.98 | $1935.96 | $19.50 | $1.56 | $21.06 | $21.06 | $989.04 | $1914.90 | 1.94R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 24 | H2 ASK | LONG | 4157.740 | 4145.33 | 4182.56 | 12.410 | 24.820 | 2.00R | 0.78 | $967.98 | $1935.96 | $19.50 | $1.56 | $21.06 | $21.06 | $989.04 | $1914.90 | 1.94R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 25 | H2 ASK | LONG | 4200.995 | 4199.80 | 4203.38 | 1.195 | 2.385 | 2.00R | 6.82 | $814.99 | $1626.57 | $170.50 | $13.64 | $184.14 | $184.14 | $999.13 | $1442.43 | 1.44R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |
| Plan 26 | H2 ASK | SHORT | 4121.195 | 4136.35 | 4090.88 | 15.155 | 30.315 | 2.00R | 0.64 | $969.92 | $1940.16 | $16.00 | $1.28 | $17.28 | $17.28 | $987.20 | $1922.88 | 1.95R | $1000.00 | RISK_BLOCKED | PLAN_NOT_ACTIVE |

---

## 5. Deep-Dive on Plan 1 and Plan 4

### Deep Dive: Plan 1 (H4 BID SHORT — Wide Stop Distance)
- **Timeframe & Series:** H4 BID
- **Entry Price:** `4227.950`
- **Stop Loss:** `4256.140` (dist: `28.190` USD/oz)
- **Take Profit:** `4171.570` (dist: `56.380` USD/oz)
- **Gross Risk/Reward:** $56.380 / 28.190 = 2.00R$
- **Total Risk per Lot:** $(28.190 \times 100) + \$2.00 + \$25.00 = \$2,819.00 + \$27.00 = \$2,846.00$
- **Allocated Lot:** $\lfloor 1000.00 / 2846.00 \rfloor_{0.01} = 0.35$ lot ($35$ troy oz)
- **Cash Flows:**
  - Gross Cash Loss: $0.35 \times 28.190 \times 100 = \$986.65$
  - Gross Cash Reward: $0.35 \times 56.380 \times 100 = \$1,973.30$
  - Commission Cost: $0.35 \times \$2.00 = \$0.70$
  - Spread Cost: $0.35 \times \$25.00 = \$8.75$
  - Total Scenario Cost: $\$0.70 + \$8.75 = \$9.45$
  - Cash Risk: $\$986.65 + \$9.45 = \$996.10 \le \$1,000.00$
  - Net Cash Reward: $\$1,973.30 - \$9.45 = \$1,963.85$
- **Net R:R:**
  $$R_{\text{net}} = \frac{\$1,963.85}{\$996.10} = 1.97153899... \implies \mathbf{1.97R}$$
- **Status:** `RISK_APPROVED` (Clean qualification, cost friction = $0.95\%$ of budget).

---

### Deep Dive: Plan 4 (H2 BID SHORT — Narrow Stop Distance)
- **Timeframe & Series:** H2 BID
- **Entry Price:** `4356.705`
- **Stop Loss:** `4357.690` (dist: `0.985` USD/oz)
- **Take Profit:** `4354.740` (dist: `1.965` USD/oz)
- **Gross Risk/Reward:** $1.965 / 0.985 = 1.9949... \implies 1.99R$
- **Total Risk per Lot:** $(0.985 \times 100) + \$2.00 + \$25.00 = \$98.50 + \$27.00 = \$125.50$
- **Allocated Lot:** $\lfloor 1000.00 / 125.50 \rfloor_{0.01} = 7.96$ lots ($796$ troy oz)
- **Cash Flows:**
  - Gross Cash Loss: $7.96 \times 0.985 \times 100 = \$784.06$
  - Gross Cash Reward: $7.96 \times 1.965 \times 100 = \$1,564.14$
  - Commission Cost: $7.96 \times \$2.00 = \$15.92$
  - Spread Cost: $7.96 \times \$25.00 = \$199.00$
  - Total Scenario Cost: $\$15.92 + \$199.00 = \$214.92$
  - Cash Risk: $\$784.06 + \$214.92 = \$998.98 \le \$1,000.00$
  - Net Cash Reward: $\$1,564.14 - \$214.92 = \$1,349.22$
- **Net R:R:**
  $$R_{\text{net}} = \frac{\$1,349.22}{\$998.98} = 1.3505976... \implies \mathbf{1.35R}$$
- **Status:** `RISK_BLOCKED` (`PLAN_NOT_ACTIVE`).
- **Cost Friction Impact:** While Plan 1 had $\$9.45$ in costs ($0.95\%$ of cash risk), Plan 4 incurs $\$214.92$ in costs ($21.5\%$ of total cash risk!). The 27-cent execution cost per ounce represents $27.4\%$ of the 98.5-cent stop buffer! Under `RiskRewardMetric.NET` with a $1.5R$ threshold, Plan 4 is blocked for `MINIMUM_RR_NOT_MET`.

---

## 6. Position Sizing & Budget Invariance Proof

A core safety guarantee of TraderLab is **Budget Invariance**: the estimated cash risk can *never* exceed the allocated risk budget under any circumstance.

### Mathematical Proof:
1. Let Account Equity be $E$, Risk Fraction be $f$, and Risk Budget be $B = \text{quantize}(E \times f, 0.01)$.
2. Let $D_{\text{stop}}$ be stop distance in account currency, $U$ be units per lot, and $C_{\text{lot}}$ be execution cost per lot ($C_{\text{comm}} + C_{\text{spread}} + C_{\text{slip}}$).
3. Total risk per 1.0 standard lot is:
   $$R_{\text{lot}} = (D_{\text{stop}} \times U) + C_{\text{lot}}$$
4. Raw unrounded lots are $L_{\text{raw}} = B / R_{\text{lot}}$.
5. Allocated lots are strictly floored to the quantity step $S$ ($S = 0.01$):
   $$L = \lfloor L_{\text{raw}} / S \rfloor \times S \implies L \le L_{\text{raw}}$$
6. Therefore, the estimated cash risk $R_{\text{cash}}$ is:
   $$R_{\text{cash}} = L \times R_{\text{lot}} \le L_{\text{raw}} \times R_{\text{lot}} = \left(\frac{B}{R_{\text{lot}}}\right) \times R_{\text{lot}} = B$$
7. Because $L \le L_{\text{raw}}$ and $R_{\text{lot}} > 0$, it is mathematically impossible for $R_{\text{cash}} > B$.
8. Inspection of all 26 plans in Section 4 demonstrates:
   $$\max_{i=1..26}(R_{\text{cash}, i}) = \$999.96 \le \$1,000.00$$
Budget invariance is preserved across 100% of assessed trade plans.

---

## 7. Broker Contract Metadata Provenance

To prevent dangerous assumptions in automated quantitative systems, broker metadata provenance is made strictly explicit:

```python
class SpecificationSourceType(enum.Enum):
    HYPOTHETICAL_BROKER_SCENARIO = "HYPOTHETICAL_BROKER_SCENARIO"
    RESEARCH_FIXTURE = "RESEARCH_FIXTURE"
    VERIFIED_LIVE_BROKER = "VERIFIED_LIVE_BROKER"
    UNKNOWN_PROVENANCE = "UNKNOWN_PROVENANCE"
```

### Audit Finding:
In the current research and backtesting configuration, TraderLab operates without live broker API keys. Therefore:
- All contract specifications are explicitly tagged `source_type = SpecificationSourceType.HYPOTHETICAL_BROKER_SCENARIO`.
- Tagging as `VERIFIED_LIVE_BROKER` is strictly prohibited unless authenticated live broker credentials return identical contract parameters.
- All explainability cards render `Contract & Broker Spec: ... | Source: HYPOTHETICAL_BROKER_SCENARIO`.

---

## 8. Portfolio Risk & Single Economic Opportunity Analysis

A critical risk management vulnerability in multi-timeframe ICT strategies is **illusory opportunity diversification**.

### Overlap Audit:
Across the 26 trade plans, exactly 4 achieved `RISK_APPROVED`:
1. **Plan 1:** H4 BID SHORT (`Entry 4227.950`, `Lots 0.35`, `Risk $996.10`)
2. **Plan 7:** H2 BID SHORT (`Entry 4236.585`, `Lots 0.50`, `Risk $991.25`)
3. **Plan 14:** H4 ASK SHORT (`Entry 4229.660`, `Lots 0.34`, `Risk $997.90`)
4. **Plan 20:** H2 ASK SHORT (`Entry 4238.210`, `Lots 0.48`, `Risk $998.40`)

### Correlated Origin Analysis:
- All 4 plans share the exact same structural trigger: Bearish BOS at `2026-09-28 00:00 UTC` and Bearish FVG formed at `2026-09-28 08:00 UTC`.
- H4 and H2 are different temporal samplings of the exact same underlying price movement.
- BID and ASK are simultaneous mirrored views separated only by the 25-tick spread.
- **Economic Reality:** These 4 plans represent **1 single economic market event**.
- **Portfolio Danger:** If a naive execution engine approved and executed all 4 plans, the aggregate cash risk would be:
  $$\$996.10 + \$991.25 + \$997.90 + \$998.40 = \$3,983.65 \quad (\approx 4.0\% \text{ account risk!})$$
- **Architectural Policy:** In future execution integration, a deduplication/correlation filter must select at most 1 plan per underlying setup candidate.

---

## 9. Point-in-Time & Bitemporal Integrity

All risk calculations satisfy strict Point-in-Time (PIT) safety guarantees:
1. **As-Of Reconstructability:** Every `IctRiskAssessment` records `risk_assessment_time`, `as_of_utc`, and references immutable structural event IDs (`related_event_ids`).
2. **Zero Future Information Leakage:** FX conversion rates, account snapshots, and candle arrays timestamped after `as_of_utc` trigger immediate `RiskRejectionReason.FUTURE_INFORMATION` rejection (verified by Test 81).
3. **No Retrospective Revisions:** Historical swings, order blocks, and trade plans evaluated at historical timestamps reproduce bit-for-bit identical IDs and parameters.

---

## 10. Intrabar Ambiguity & Order Execution Disclaimer

1. **Zero Execution Leakage:** The Risk Management Engine operates in pure domain memory. It does NOT:
   - Connect to brokers, FIX engines, or REST trading endpoints.
   - Place, submit, modify, or cancel orders.
   - Create fills, partial executions, or slippage models on live accounts.
   - Track live PnL or margin utilization.
2. **Intrabar Ambiguity Detection:** When historical closed candles are provided to `evaluate_trade_plan_risk`, if a single bar touches both the stop-loss and the primary target, the engine assigns `RiskRejectionReason.AMBIGUOUS_INTRABAR_SEQUENCE` and blocks the plan from analytical qualification.

---

## 11. Test Suite Expansion & Regression Coverage

In accordance with Stage 8, exactly 20 new comprehensive tests were implemented in `tests/test_ict_risk_management.py`. All 893 baseline tests were preserved without alteration:

| Test ID | Test Name | Target Verification | Status |
| :--- | :--- | :--- | :--- |
| **66** | `test_net_rr_plan1_matches_cash_flow_math` | Exact Plan 1 cash flows: $986.65 loss, $1973.30 reward, $9.45 cost -> 1.97R | PASSED |
| **67** | `test_net_rr_narrow_stop_plan4` | Narrow stop Plan 4: 0.985 dist, 0.27 cost (27.4% friction), Net RR = 1.35R | PASSED |
| **68** | `test_gross_rr_differs_from_net_rr_with_costs` | Mathematical proof that Net RR < Gross RR when costs > 0 | PASSED |
| **69** | `test_net_rr_unknown_cost_is_none` | Unknown cost protection: Net RR is None, no assumptions made | PASSED |
| **70** | `test_stop_cost_and_target_cost_separate` | Asymmetric stop cost vs target cost evaluation ($1.00 vs $0.20) | PASSED |
| **71** | `test_round_trip_commission_semantics` | Round-trip commission flag and execution cost per lot verification | PASSED |
| **72** | `test_spread_unit_conversion` | Tick-to-lot conversion: 25 ticks * 0.01 * 100 = $25.00/lot | PASSED |
| **73** | `test_bid_ask_spread_not_double_counted` | Clean spread accounting without artificial entry/exit duplication | PASSED |
| **74** | `test_slippage_cost_is_considered_when_defined` | Slippage ticks adjustment: allocated lots reduce, budget guarded | PASSED |
| **75** | `test_fx_conversion_preserves_currency_units` | Cross-currency (EURUSD -> EUR account) conversion invariance | PASSED |
| **76** | `test_position_size_recomputed_after_cost_change` | Dynamic re-sizing when spreads widen from 10 to 100 ticks | PASSED |
| **77** | `test_final_cash_risk_never_exceeds_budget` | Property-based simulation over 100 distance/equity variations | PASSED |
| **78** | `test_minimum_rr_uses_configured_gross_or_net_metric` | Configurable evaluation: Plan 4 blocked under NET, passes under GROSS | PASSED |
| **79** | `test_missing_broker_source_not_verified` | Unauthenticated broker spec forces HYPOTHETICAL provenance | PASSED |
| **80** | `test_hypothetical_broker_metadata_label` | Audit card string includes HYPOTHETICAL_BROKER_SCENARIO label | PASSED |
| **81** | `test_historical_account_snapshot_does_not_leak` | Future FX rate / account snapshot rejected under PIT cutoff | PASSED |
| **82** | `test_bid_ask_mirrored_opportunity_not_double_counted` | Structural setup linkage detection across BID and ASK trade plans | PASSED |
| **83** | `test_missing_portfolio_snapshot_prevents_full_approval` | Max open portfolio risk ratio breach triggers RISK_BUDGET_EXCEEDED | PASSED |
| **84** | `test_plan_status_does_not_imply_actual_fill` | Zero-order, zero-fill, zero-execution invariance verification | PASSED |
| **85** | `test_all_26_risk_assessments_reconcile` | 26-plan batch reconciliation: 4 approved, 22 blocked, all risk <= budget | PASSED |

---

## 12. Quality Gate Verification

All quality gates passed with zero warnings or errors:
- **Pytest:** `913 passed in 142.10s` (Original 893 + 20 new tests = 913 tests).
- **Ruff Linter:** `ruff check src/ tests/` -> `All checks passed!` (0 errors).
- **Ruff Formatter:** `ruff format --check src/ tests/` -> `112 files already formatted` (0 unformatted files).
- **Mypy Static Typing:** `mypy src/traderlab/` -> `Success: no issues found in 73 source files` (Strict typing verified).

---

## 13. Latency & Performance Profile

End-to-end execution benchmark across real Dukascopy M1 historical data for the full pipeline (Data Loading $\to$ Resampling $\to$ Market Structure $\to$ Setups $\to$ 26 Plans $\to$ Risk Assessment):

| Pipeline Sub-stage | Execution Time | Share (%) |
| :--- | :--- | :--- |
| **Data Loading (M1 CSV Ingestion)** | 2.1581s | 60.6% |
| **Resampling (M1 $\to$ D1, H4, H2)** | 1.2630s | 35.5% |
| **Existing Structural Features (Swings, FVG, OB, MSS)** | 0.1283s | 3.6% |
| **ICT Setup Candidate Assembly** | 0.0029s | 0.1% |
| **Trade Plan Generation (26 Plans)** | 0.0018s | <0.1% |
| **Stop-Loss Calculation (26 Plans)** | 0.0001s | <0.1% |
| **Take-Profit Calculation (26 Plans)** | 0.0001s | <0.1% |
| **Risk/Reward Math (26 Plans)** | 0.0001s | <0.1% |
| **Position Sizing Engine (26 Plans)** | 0.0016s | <0.1% |
| **Point-in-Time Snapshot Filtering** | 0.0001s | <0.1% |
| **Total End-to-End Pipeline** | **3.5622s** | **100.0%** |

The pure domain risk calculation for all 26 plans completes in **under 2 milliseconds** ($<0.08\text{ ms per plan}$).

---

## 14. Architectural Integrity & Boundaries

- **Zero Git Commits / Pushes:** Git index untouched, work contained in working directory.
- **Zero Database Operations:** No PostgreSQL queries, mutations, or migrations.
- **Zero Raw Data Mutations:** No CSV edits or data downloads.
- **Strict Decimal Arithmetic:** No `float` types used in monetary, price, or lot calculations.
- **Zero Execution Leakage:** Complete decoupling from broker APIs, order placement, and live execution.

---

## 15. Final Acceptance Statement & Declarations

### Audit Declaration:
1. **Mathematical Reconciliation Confirmed:** Plan 1 Net R:R is conclusively reconciled to $1.97R$ (Gross R:R $2.00R$), and Plan 4 is conclusively reconciled to $1.35R$ (Gross R:R $1.99R$), matching exact cash-flow dynamics to the penny.
2. **Cost & Sizing Consistency Verified:** Execution costs (commission, spread, slippage) are unified between position sizing and Net R:R calculation.
3. **Budget Invariance Proven:** No position allocation ever exceeds the allocated risk budget.
4. **All 26 Historical Trade Plans Audited:** 4 plans are qualified as `RISK_APPROVED`, 22 plans are verified as `RISK_BLOCKED`, matching 100% of theoretical expectations.
5. **Quality Gates Cleared:** All 913 tests passing, zero linting or formatting defects, clean type annotations.

**FAZ 4E-B-K1: ACCEPTED — RISK MATHEMATICS & COST INTEGRITY VERIFIED**
