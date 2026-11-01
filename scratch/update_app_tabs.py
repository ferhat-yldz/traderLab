import re
from pathlib import Path

app_path = Path("src/traderlab/interfaces/dashboard/app.py")
content = app_path.read_text(encoding="utf-8")

# Pattern: in main(), find from '    with tab_chart:\n' to '    # ── Tab 2: Veri Envanteri'
# Note that render_single_chart_dashboard doesn't have '# ── Tab 2: Veri Envanteri' inside it.
pattern = r"(    # ── Tab 1: Interactive Candlestick Chart ──+\n    with tab_chart:\n).*?(    # ── Tab 2: Veri Envanteri & GitHub Güncellik Paneli ──+\n    with tab_inventory:)"

replacement = r"""\1        if show_mtf:
            render_mtf_dashboard(
                start_utc=start_utc,
                end_utc=end_utc,
                as_of_utc=as_of_utc,
                selected_price_type=selected_price_type,
                selected_daily_mode=selected_daily_mode,
                daily_mode_str=daily_mode_str,
                price_type_str=price_type_str,
                chart_type=chart_type,
                show_synthetic=show_synthetic,
                selected_range=selected_range,
                available_months=local_summary.available_months,
                show_fvg=show_fvg,
                show_bullish_fvg=show_bullish_fvg,
                show_bearish_fvg=show_bearish_fvg,
                show_ce_line=show_ce_line,
                show_ifvg=show_ifvg,
                active_fvg_only=active_fvg_only,
            )
        else:
            render_single_chart_dashboard(
                start_utc=start_utc,
                end_utc=end_utc,
                as_of_utc=as_of_utc,
                selected_price_type=selected_price_type,
                selected_timeframe=selected_timeframe,
                timeframe_str=timeframe_str,
                selected_daily_mode=selected_daily_mode,
                daily_mode_str=daily_mode_str,
                price_type_str=price_type_str,
                chart_type=chart_type,
                view_mode=view_mode,
                show_synthetic=show_synthetic,
                selected_range=selected_range,
                range_options=range_options,
                default_tf_map=default_tf_map,
                available_months=local_summary.available_months,
                show_fvg=show_fvg,
                show_bullish_fvg=show_bullish_fvg,
                show_bearish_fvg=show_bearish_fvg,
                show_ce_line=show_ce_line,
                show_ifvg=show_ifvg,
                active_fvg_only=active_fvg_only,
            )

\2"""

new_content, count = re.subn(pattern, replacement, content, flags=re.DOTALL)
print(f"Substitutions made: {count}")
if count == 1:
    app_path.write_text(new_content, encoding="utf-8")
    print("Successfully updated app.py!")
else:
    print("Error: Pattern did not match exactly once.")
