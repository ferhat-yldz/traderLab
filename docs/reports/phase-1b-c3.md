# TRADERLAB — FAZ 1B-C3 RAPORU
## Veri Bütünlüğü Kontrolü ve 2024 XAUUSD Tarihsel Arşivinin Tamamlanması

**Tarih:** 21 Ekim 2026  
**Durum:** READY  
**Önceki Rapor:** `docs/reports/phase-1b-c2-ui-fix.md`  
**Kapsam:** Veri Bütünlüğü Geçiş Kapısı (Data Integrity Gate), Ekim 2026 Uyuşmazlık Çözümü, Hafta Sonu Rangebreaks Düzeltmesi, 2024 Şubat–Aralık BID/ASK M1 İndirmesi, Yıllık Veri Denetimi ve Dashboard Genişletmesi

---

### 1. YÖNETİCİ ÖZETİ VE KARAR

Faz 1B-C3 kapsamında, harici veri deposundaki yeni dosyalar indirilmeden önce zorunlu **DATA INTEGRITY GATE (Veri Bütünlüğü Kapısı)** çalıştırılmış ve raporda tespit edilen soru işaretleri matematiksel/yazılımsal olarak tam çözüme kavuşturulmuştur:

1. **Ekim 2026 Satır Sayısı Açıklığa Kavuştu:** CSV dosyalarındaki gerçek satır sayısı BID için **17.400**, ASK için **17.400** satırdır. Önceki raporda geçen "27.000" sayısı, 19 günlük takvim süresinin teorik dakika kurgusundan (`18.75 gün × 1.440 dk ≈ 27.000`) kaynaklanan bir belgelendirme karışıklığıdır. Ekim 2026 CSV'sinde hafta sonu piyasa kapalı dakikaları satır olarak depolanmadığından ham satır sayısı 17.400'dür (0 tekrar, 0 bozuk veri).
2. **18–19 Ekim 2026 Fiyat Uyuşmazlığı Doğrulandı:** Ham CSV dosyalarındaki gerçek XAUUSD fiyatları **4.150 – 4.185 USD** seviyesindedir (18 Ekim 22:00 UTC açılış: `4.162,335 USD`, 23:59 UTC: `4.184,735 USD`; 19 Ekim 13:59 UTC son bar: `4.154,495 USD`). Terminal grafik arayüzü de bu gerçek değerleri göstermektedir. Önceki rapordaki "2.716 USD" ifadesinin rapor yazarının Ekim 2024 güncel altın fiyatlarıyla 2026 sentetik veri setini karıştırmasından kaynaklanan bir **yazım/dokümantasyon hatası** olduğu doğrulanmış ve asıl CSV'ye dokunulmadan markdown raporu düzeltilmiştir.
3. **Pazar Akşamı Mumlarının Görünürlüğü Düzeltildi:** Plotly grafik konfigürasyonundaki `rangebreaks=[dict(bounds=["sat", "mon"])]` ayarının Pazar 22:00–23:59 UTC arasındaki gerçek Forex işlem barlarını gizlediği tespit edilmiş ve bu filtre kaldırılmıştır. Artık Pazar haftalık açılış mumları tüm zaman dilimlerinde (M1, M15, H1, H4, D1) eksiksiz görüntülenmektedir.
4. **2024 Tarihsel Arşivi Eksiksiz Tamamlandı:** Veri bütünlüğü kapısı **PASS** verdikten sonra, 2024 yılının eksik 11 ayı (Şubat–Aralık) BID ve ASK M1 CSV formatında (toplam 22 yeni dosya) sparse-checkout ile yerel depoya aktarılmıştır. 2024 yılı toplam **527.040 M1 satırı / 366 gün** ile %100 kapsama ve 0 negatif spread ile doğrulanmıştır.

Karar: **READY** (2024 yılı tam arşivi eksiksiz kullanıma hazırdır).

---

### 2. DATA INTEGRITY GATE (VERİ BÜTÜNLÜĞÜ GEÇİŞ KAPISI) ANALİZİ

#### A. Ekim 2026 Satır Sayısı Mutabakatı

| Parametre | BID Değeri | ASK Değeri | Açıklama |
|:---|:---|:---|:---|
| **Ham CSV Satır Sayısı** | **17.400** | **17.400** | Dosyadaki gerçek veri satırı (başlık hariç) |
| **Tekrarlanan Timestamp** | 0 | 0 | Mükerrer kayıt yoktur |
| **diff == 60.000 ms (Seans İçi)** | **17.387** | **17.387** | Açık seans boyunca adımlar kesin 60.000 ms |
| **diff > 60.000 ms (Kapalı Seans)**| **12 gap** | **12 gap** | 3 hafta sonu (49 saat) + 9 rollover (61 dk) |
| **İlk Bar Açılışı** | 2026-10-01 00:00:00 UTC | 2026-10-01 00:00:00 UTC | Ay başı |
| **Son Bar Açılışı** | 2026-10-19 13:59:00 UTC | 2026-10-19 13:59:00 UTC | Veri kesintisi |
| **Takvimsel Süre (Dakika)** | 26.760 dk (~27.000 dk) | 26.760 dk (~27.000 dk) | 18 gün 14 saatlik takvim penceresi |
| **Cumartesi / Pazar Gündüz Satırı** | 0 satır | 0 satır | Hafta sonu kapalı saatler CSV'de yer almaz |
| **Pazar Akşamı (22:00-24:00 UTC)** | 360 satır (120/hafta) | 360 satır (120/hafta) | 3 haftanın pazar açılışları eksiksiz |

#### 2024 ve 2026 Veri Formatı / Session Policy Karşılaştırması
- **2024 Yılı Formatı (Continuous Calendar-Minute):** 2024 yılı dosyaları tam takvim ızgarası kullanmaktadır. Hafta sonu (Cumartesi tüm gün ve Pazar 22:00 öncesi) ve tatil kapalı seansları, indirici (downloader) tarafından sentetik flat barlarla (`open == high == low == close`) doldurulmuştur (%31,8 sentetik bar oranı). Bu nedenle 2024 dosyalarında `diff > 60.000 ms` olan 0 adet boşluk vardır; tüm adımlar kesintisiz 60.000 ms'dir (Toplam 366 gün × 1.440 = 527.040 satır).
- **Ekim 2026 Formatı (Trading-Session-Only / Sparse):** Ekim 2026 dosyasında ise kapalı seanslar ve hafta sonları sentetik barlarla doldurulmamış, doğrudan atlanmıştır. Bu nedenle dosyada 0 adet Cumartesi satırı ve tam 12 adet doğal piyasa kapalı boşluğu (3 adet 49 saatlik hafta sonu, 9 adet 61 dakikalık rollover molası) bulunmaktadır.
- **Nihai Teşhis:** **`C) Yıllar arasında format/policy farklı.`** (2024 yılı sentetik doldurulmuş continuous calendar-minute; Ekim 2026 ise kapalı seansları filtrelenmiş trading-session-only'dir. Her iki yaklaşım da kendi içinde %100 tutarlıdır ve açık piyasa saatlerinde 0 eksik dakikaya sahiptir).

#### B. 18–19 Ekim 2026 Fiyat Düzeyi Denetimi

Ham CSV dosyalarından doğrudan okunan doğrulanmış fiyatlar:

| Hedef Zaman (UTC) | BID (Open / High / Low / Close) | ASK (Open / High / Low / Close) | Spread |
|:---|:---|:---|:---|
| **2026-10-16 20:00** | 4168.635 / 4168.635 / 4167.315 / 4167.315 | 4169.165 / 4169.165 / 4167.835 / 4167.867 | 0.552 |
| **2026-10-16 20:59 (Cuma Kapanış)** | 4164.965 / 4165.825 / 4164.355 / 4165.275 | 4165.861 / 4166.865 / 4165.545 / 4165.555 | 0.280 |
| **2026-10-18 22:00 (Pazar Açılış)** | **4162.335** / 4163.241 / 4155.255 / **4163.095** | **4164.935** / 4164.935 / 4158.042 / **4163.769** | 0.674 |
| **2026-10-18 23:59 (Pazar Bitiş)** | 4183.335 / 4184.925 / 4183.195 / **4184.735** | 4183.842 / 4185.475 / 4183.802 / **4185.333** | 0.598 |
| **2026-10-19 00:00 (Pazartesi)** | 4184.579 / 4186.677 / 4184.261 / 4185.611 | 4185.075 / 4187.205 / 4184.828 / 4186.155 | 0.544 |
| **2026-10-19 12:00** | 4161.171 / 4161.469 / 4159.606 / 4159.705 | 4161.612 / 4161.952 / 4160.092 / 4160.212 | 0.507 |
| **2026-10-19 13:59 (Son Kayıt)** | 4153.865 / 4155.035 / 4153.235 / **4154.495** | 4154.376 / 4155.555 / 4153.727 / **4155.079** | 0.584 |

**Uyuşmazlığın Kesin Nedeni:**
- CSV dosyaları, Python okuyucu ve Plotly terminal grafiği **4.150–4.185 USD** fiyatlarını kusursuz işlemektedir.
- "2.716 USD" ibaresi terminal ekranında veya kodda hiç var olmamıştır; yalnızca önceki rapor metninde yazarın Ekim 2024 piyasa fiyatını sehven yazmasından ibarettir.
- [phase-1b-c2-ui-fix.md](file:///c:/trader/traderLab/traderlab/docs/reports/phase-1b-c2-ui-fix.md) dosyasındaki bu yazım hatası `4.162,33 -> 4.184,73 USD` olarak düzeltilmiştir.

#### C. Pazar Akşamı Mum Görünürlüğü (Rangebreaks Düzeltmesi)
- Plotly'deki `rangebreaks=[dict(bounds=["sat", "mon"])]` kuralı Cumartesi 00:00 ile Pazartesi 00:00 arasındaki tüm zaman dilimini gizlemekteydi.
- Bu durum Pazar 22:00–23:59 UTC arasındaki 120 adet gerçek Forex M1 mumunun ekranda görünmesini engelliyordu.
- `charts.py` içerisinden bu rangebreak kaldırılmış, gerçek piyasa verisinin zaman ekseninde doğal akışıyla gösterilmesi sağlanmıştır.
- Tarayıcıda 18 Ekim 2026 H1 ve M1 görünümleri test edilmiş ve 22:00–23:00 barlarının başarıyla çizildiği ekran görüntüsüyle doğrulanmıştır.

**DATA INTEGRITY GATE SONUCU:** **`PASS`**

---

### 3. İNDİRİLEN 2024 YILI VERİ ENVANTERİ VE DOĞRULAMA

Market-Data-Lab Git reposundan (`commit: 78fd60f8bf3a1cebe198828147e54e4ed32385d8`) sparse-checkout ile 2024 yılının 11 eksik ayı (22 CSV dosyası) indirilmiştir.

#### 2024 Tüm Aylar Finansal Kalite Matrisi

| Ay | BID Satır | ASK Satır | Matched | Min Spread | Max Spread | Sentetik % | BID SHA-256 (İlk 16 hane) | ASK SHA-256 (İlk 16 hane) |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| **2024-01** | 44.640 | 44.640 | EVET (%100) | 0.085 | 2.490 | %31,52 | `6fa24d76649fec4f` | `25139c9795be9370` |
| **2024-02** | 41.760 | 41.760 | EVET (%100) | 0.005 | 5.981 | %30,60 | `2b88348416e6e4cf` | `888ba3031070868f` |
| **2024-03** | 44.640 | 44.640 | EVET (%100) | 0.015 | 1.710 | %34,95 | `665f75b22b27bc80` | `4f3cefd3329ebfe9` |
| **2024-04** | 43.200 | 43.200 | EVET (%100) | 0.001 | 2.810 | %29,72 | `892dcacd2b202fe8` | `4bfe7d54483a93e3` |
| **2024-05** | 44.640 | 44.640 | EVET (%100) | 0.100 | 2.107 | %29,17 | `eec01f5f3e9a7e80` | `82b49dff21e84d6d` |
| **2024-06** | 43.200 | 43.200 | EVET (%100) | 0.005 | 1.687 | %35,83 | `b85c456af930777e` | `a3e2e0e227fc6ea6` |
| **2024-07** | 44.640 | 44.640 | EVET (%100) | 0.085 | 3.954 | %28,90 | `c31d65fb58852ca2` | `e2a149c99df34027` |
| **2024-08** | 44.640 | 44.640 | EVET (%100) | 0.022 | 1.980 | %32,26 | `ae8b399cc1aa04df` | `84bbfebe9c18df8a` |
| **2024-09** | 43.200 | 43.200 | EVET (%100) | 0.043 | 3.224 | %32,64 | `a42e0f316262c3e1` | `b99e746a51d4e0e6` |
| **2024-10** | 44.640 | 44.640 | EVET (%100) | 0.001 | 2.954 | %28,90 | `e88c9a1e0db02fc3` | `beea2ea84b55be4f` |
| **2024-11** | 43.200 | 43.200 | EVET (%100) | 0.025 | 4.204 | %33,19 | `7467dede538c8c50` | `b91a7e58a2f4a475` |
| **2024-12** | 44.640 | 44.640 | EVET (%100) | 0.001 | 5.120 | %34,14 | `c4d3030616b7724a` | `0d306b537d995914` |
| **TOPLAM** | **527.040** | **527.040** | **%100** | **-** | **-** | **~%31,8** | **12 Dosya** | **12 Dosya** |

*Not: 2024 yılı artık yıldır (leap year); Şubat ayı 29 gün = 41.760 satırdır. Yıllık toplam takvim dakikası 366 × 1.440 = 527.040 dakikadır.*

---

### 4. 2024 YAZ/KIŞ SAATİ (DST) GEÇİŞLERİ DOĞRULAMASI

ABD New York seans kapanış saatlerinin UTC karşılıkları 2024 geçiş tarihlerinde test edilmiştir:
- **İlkbahar DST (10 Mart 2024):**
  - Cuma 8 Mart 2024 (Kış saati - EST UTC-5): Kapanış 22:00 UTC (17:00 EST).
  - Cuma 15 Mart 2024 (Yaz saati - EDT UTC-4): Kapanış 21:00 UTC (17:00 EDT).
- **Sonbahar DST (3 Kasım 2024):**
  - Cuma 1 Kasım 2024 (Yaz saati - EDT UTC-4): Kapanış 21:00 UTC (17:00 EDT).
  - Cuma 8 Kasım 2024 (Kış saati - EST UTC-5): Kapanış 22:00 UTC (17:00 EST).
Tüm geçişler `XAUUSDTradingCalendar` ve resampling motoru tarafından doğru tespit edilmiştir.

---

### 5. TARAYICI DOĞRULAMASI VE EKRAN GÖRÜNTÜLERİ

| Senaryo | Dosya Yolu | Doğrulanan Özellik |
|:---|:---|:---|
| **Haziran 2024 — M15** | [june_2024_m15.png](file:///c:/trader/traderLab/traderlab/docs/reports/screenshots/june_2024_m15.png) | 14 Haziran 2024 M15 görünümü, %H:%M saat ekseni |
| **Haziran 2024 — D1 (30 Gün)** | [june_2024_d1.png](file:///c:/trader/traderLab/traderlab/docs/reports/screenshots/june_2024_d1.png) | 30 adet günlük mumdan oluşan June 2024 D1 serisi |
| **Aralık 2024 — D1 (31 Gün)** | [december_2024_d1.png](file:///c:/trader/traderLab/traderlab/docs/reports/screenshots/december_2024_d1.png) | 31 adet günlük mumdan oluşan December 2024 D1 serisi |
| **Pazar 18 Ekim 2026 — H1** | [sunday_oct_18_h1.png](file:///c:/trader/traderLab/traderlab/docs/reports/screenshots/sunday_oct_18_h1.png) | Pazar 22:00 ve 23:00 UTC mumlarının görünürlüğü (rangebreak kalktı) |
| **Envanter Tablosu (26 Dosya)**| [tab2_inventory_26_files.png](file:///c:/trader/traderLab/traderlab/docs/reports/screenshots/tab2_inventory_26_files.png) | 12 ay 2024 + 2026-10 = 26 dosyalık tam envanter |

---

### 6. DEĞİŞTİRİLEN VE EKLENEN KOD DOSYALARI

1. `src/traderlab/interfaces/dashboard/charts.py`:
   - Hafta sonu Pazar barlarını gizleyen `rangebreaks` kaldırıldı.
   - `empty_message` güncellendi ("2024 Tüm Aylar ve 2026-10").
2. `src/traderlab/interfaces/dashboard/app.py`:
   - Sidebar dönem ön ayarlarına 2024 mevsimsel ve yıl sonu seçenekleri eklendi.
3. `tests/test_dashboard_inventory.py`:
   - `TestLocalDataInventory` 26 dosya ve 13 mevcut aya uyarlandı.
   - `test_october_2026_exact_row_count_and_no_duplicates` eklendi.
   - `test_october_2026_prices_in_csv_are_4150_to_4185_range` eklendi.
   - `test_sunday_candles_visible_on_chart_no_rangebreaks_hiding` eklendi.
   - `Test2024FullArchiveAndMultiMonthSupport` sınıfı eklendi (Aylık D1 resample, DST geçişleri, BID/ASK spread testleri).
4. `docs/reports/phase-1b-c2-ui-fix.md`:
   - Raporlama yazım hatası olan 27.000 / 2.716 USD değerleri asıl CSV doğrulamalarıyla düzeltildi.

---

### 7. TEST VE KALİTE KONTROL ÇIKTILARI

```bash
============================ 290 passed in 17.54s =============================
290 tests collected in 1.27s

ruff check src/ tests/
All checks passed!

ruff format --check src/ tests/
77 files already formatted

mypy src/traderlab/
Success: no issues found in 58 source files

pytest tests/integration/ (PostgreSQL)
============================= 21 passed in 1.00s ==============================
```

**Toplam Doğrulanmış Test Sayısı:** **311 test** (290 birim/fonksiyonel + 21 PostgreSQL entegrasyon).

---

### 8. 2025–2026 PAKETLERİNE GEÇİŞ TAVSİYESİ

1. **2024 Arşivi Tamdır:** 2024 yılı 12 ay boyunca eksiksiz, kesintisiz M1 verisi sunmakta olup kantitatif modelleme, özellik mühendisliği (feature engineering) ve piyasa yapısı (market structure) araştırmaları için birinci sınıf altın referans veri setidir.
2. **2025 ve 2026 Paketleri İçin Yol Haritası:**
   - Bir sonraki aşamada (Faz 1B-C4), benzer kontrollü sparse-checkout stratejisi ile 2025 yılının 12 ayı getirilebilir.
   - 2026 yılı için ise Ocak–Eylül 2026 ayları indirilerek Ekim 2026 ile birleştirilebilir ve 2024–2026 arası 3 yıllık kesintisiz XAUUSD tarihsel arşivi tamamlanabilir.

---

### 9. SONUÇ VE STATÜ

TRADERLAB veri altyapısı, şüpheli veri yorumlarını başarıyla temizlemiş, gerçek CSV fiyatlarıyla tam mutabakat sağlamış ve 2024 yılının 527.040 dakikalık devasa tarihsel BID/ASK arşivini sıfır hata ve kusursuz grafik görünürlüğüyle bünyesine katmıştır.

**Nihai Karar:** **`READY`**
