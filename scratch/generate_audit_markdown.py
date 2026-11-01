import csv
from pathlib import Path

csv_path = Path("docs/reports/w1_candle_audit_table.csv")
rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))

md_out = Path("scratch/w1_audit_tables.md")

with open(md_out, "w", encoding="utf-8") as f:
    f.write("# TRADERLAB — 147 Haftalık W1 Mum Denetim Tablosu\n\n")
    f.write("### Eylül – Ekim 2026 Haftaları (TRADING_SESSION_ONLY & Saf Gerçek Analizi)\n\n")
    f.write(
        "| Hafta # | week_start_utc | active_m1_count | synthetic_m1_count | missing_m1_count | quality_class | w1_open | w1_high | w1_low | w1_close |\n"
    )
    f.write("|---|---|---|---|---|---|---|---|---|---|\n")
    for r in rows[138:]:
        f.write(
            f"| **{r['week_index']}** | `{r['week_start_utc']}` | **{int(r['active_m1_count']):,}** | **{int(r['synthetic_m1_count']):,}** | {int(r['missing_m1_count']):,} | **`{r['quality_class']}`** | `{r['w1_open']}` | `{r['w1_high']}` | `{r['w1_low']}` | `{r['w1_close']}` |\n"
        )

    f.write("\n\n### 2024–2026 Yıllık Dağılım ve Özet Mutabakatı\n\n")
    f.write("| Kalite Sınıfı | Haftalık Mum Sayısı | Yüzde | Açıklama |\n")
    f.write("|---|---|---|---|\n")
    f.write(
        "| **`PURE_REAL` (Saf Gerçek)** | **8 Mum** | %5.44 | 2026 Sonbahar (Eylül–Ekim). Sıfır sentetik kayıt, tam aktif seans (~6.750–6.900 aktif M1 barı). |\n"
    )
    f.write(
        "| **`MIXED` (Karma)** | **138 Mum** | %93.88 | 2024 – Ağustos 2026. Binlerce aktif kotasyon + hafta sonu ve rollover sentetik carry-forward kayıtları. |\n"
    )
    f.write(
        "| **`PARTIAL` (Kısmi)** | **1 Mum** | %0.68 | 18–19 Ekim 2026 kesinti haftası (960 aktif M1 barı, hafta tamamlanmadan veri sonlandı). |\n"
    )
    f.write(
        "| **`PURE_SYNTHETIC` (Tam Sentetik)**| **0 Mum** | %0.00 | **Arşivde sıfır aktif kotasyon içeren hiçbir hafta bulunmamaktadır.** |\n"
    )
    f.write(
        "| **TOPLAM** | **147 Mum** | **%100.00** | **34 Aylık (Ocak 2024 – 19 Ekim 2026) Tüm Yerel Arşiv** |\n"
    )

print("Generated markdown summary at:", md_out)
