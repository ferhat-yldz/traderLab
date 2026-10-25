"""Forex market adapter — the first research market.

Forex-specific concerns:
    - 24/5 market with Asian, London, New York sessions
    - Prices quoted in pips (1 pip = 0.0001 for most pairs, 0.01 for JPY pairs)
    - Volume is typically tick volume, not real volume
    - Costs: spread + commission + swap (overnight)
    - XAUUSD is the initial research instrument
"""
