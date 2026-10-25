# TRADERLAB — FAZ 1C VERİ AKTARIMI VE NİHAİ KABUL RAPORU
## 34 Aylık 2.004.825 Adet Temiz XAUUSD M1 Kotasyonunun PostgreSQL Bitemporal Tablolarına Aktarımı, Doğrudan SQL Bütünlük Doğrulaması, Ham CSV vs PostgreSQL OHLC Eşleşmesi ve 327 Testlik Kapanış Belgesi

**Rapor Tarihi:** 24 Ekim 2026  
**Yazar:** TRADERLAB Quantitative Data Engineering & Financial Software Architecture Team  
**Proje Ana Dizini:** `C:\trader\traderLab\traderlab`  
**Veri Deposu:** `C:\trader\traderLab\Market-Data-Lab` (Git Commit: `78fd60f8bf3a1cebe198828147e54e4ed32385d8` — 68 CSV Dosyası, 34 Ay, Dokunulmamış/Temiz)  
**Hedef Veritabanı:** `PostgreSQL 14.19` (`localhost:5432`, Veritabanı: `traderlab`, Şema: `public`)  
**Geri Dönüş / Yedek Tablosu:** `candles_backup_pre_phase1c` (Aktarım öncesi snapshot yedeği)  
**Karşılaştırma Dosyası:** [`docs/reports/phase_1c_raw_csv_vs_postgres_comparison.csv`](file:///c:/trader/traderLab/traderlab/docs/reports/phase_1c_raw_csv_vs_postgres_comparison.csv) (12 Farklı Zaman Damgası için Birebir OHLC Karşılaştırması)  
**Faz 1C Durumu:** **`ACCEPTED — CLOSED`** (Nihai Kabul Verildi — Resmen Kapatıldı)

---

## 1. Yönetici Özeti ve Nihai Mutabakat

Kullanıcının Faz 1C kabul ve kapanış direktifleri doğrultusunda, PostgreSQL veritabanında salt okunur (`SELECT`) doğrulamalar tamamlanmış, ham CSV kaynakları ile veritabanı kayıtları 12 kritik zaman damgasında birebir karşılaştırılmış ve sentetik eleme istatistikleri nihai toplamlarıyla tescil edilmiştir:

| Metrik / Kontrol Alanı | Hedeflenen Mutabakat | Gerçekleşen PostgreSQL Değeri | Doğrulama Durumu |
|---|---|---|---|
| **Aktarılan Temiz BID M1 Satırı** | **1.002.412** | **1.002.412** | **%100 EKSİKSİZ** |
| **Aktarılan Temiz ASK M1 Satırı** | **1.002.413** | **1.002.413** | **%100 EKSİKSİZ** |
| **Toplam Aktarılan XAUUSD M1 Satırı** | **2.004.825** | **2.004.825** | **%100 EKSİKSİZ** |
| **Elenen Sentetik BID Satırı** | **423.270** | **423.270** | **SIFIR SENTETİK SIZMA** |
| **Elenen Sentetik ASK Satırı** | **423.270** | **423.270** | **SIFIR SENTETİK SIZMA** |
| **TOPLAM ELENEN SENTETİK KAYIT** | **846.540** | **846.540** | **%100 TEMİZLENDİ** |
| **Veritabanındaki Sentetik Kayıt Sayısı** | **0** | **0** | **DOĞRULANDI (`is_synthetic=TRUE` yok)** |
| **Eşleşen Aktif Zaman Damgası** | **1.002.412** | **1.002.412** | **%100 EŞLEŞME** |
| **Yalnızca ASK Olan Kayıt** | **1 (`2026-08-16 23:59:00 UTC`)** | **1 (`2026-08-16 23:59:00 UTC`)** | **KORUNDU (Sahte BID üretilmedi)** |
| **Yalnızca BID Olan Kayıt** | **0** | **0** | **SIFIR ASİMETRİ** |
| **Mükerrer / Çift Kayıt Sayısı** | **0** | **0** | **SIFIR DUPLICATE** |
| **Negatif Kapanış Spread'i Sayısı** | **0** | **0** | **SIFIR ANORMALLİK (`ASK < BID` yok)** |
| **Ham CSV vs PostgreSQL OHLC Eşleşmesi** | **24 / 24 Kotasyon** | **24 / 24 Birebir Eşit** | **%100 TAM SAYISAL EŞİTLİK** |
| **Otomasyon Testleri (Pytest)** | **327 Test** | **327 Passed (%100)** | **SIFIR HATA / SIFIR REGRESYON** |

---

## 2. Madde 1: PostgreSQL Veri Bütünlüğünün Doğrudan Doğrulanması (Read-Only SELECT)

Mevcut PostgreSQL veritabanında kullanıcının talep ettiği kısıtlar (`instrument_id='XAUUSD'`, `timeframe=60`, `source_id='market_data_lab_m1'`, `revision=0`) altında yalnızca salt okunur (`SELECT`) sorguları çalıştırılmıştır:

### 2.1. Sorgular ve Doğrudan Konsol Çıktıları

```sql
-- 1. Fiyat Türüne Göre Aktif Kayıt Sayıları (BID / ASK)
SELECT price_type, COUNT(*) 
FROM candles 
WHERE instrument_id = 'XAUUSD' 
  AND timeframe = 60 
  AND source_id = 'market_data_lab_m1' 
  AND revision = 0
GROUP BY price_type 
ORDER BY price_type;

 price_type |  count  
------------+---------
 ASK        | 1002413
 BID        | 1002412
(2 rows)
-- Sonuç: BID = 1.002.412, ASK = 1.002.413, Toplam = 2.004.825 (DOĞRULANDI)

-- 2. Sentetik Kayıt Sayısı Kontrolü
SELECT COUNT(*) 
FROM candles 
WHERE instrument_id = 'XAUUSD' 
  AND timeframe = 60 
  AND source_id = 'market_data_lab_m1' 
  AND revision = 0
  AND is_synthetic = TRUE;

 count 
-------
     0
(1 row)
-- Sonuç: Veritabanında tek bir sentetik kayıt dahi YOKTUR (DOĞRULANDI)

-- 3. Mükerrer Kayıt Sayısı Kontrolü
SELECT open_time, price_type, COUNT(*) 
FROM candles 
WHERE instrument_id = 'XAUUSD' 
  AND timeframe = 60 
  AND source_id = 'market_data_lab_m1' 
  AND revision = 0
GROUP BY open_time, price_type 
HAVING COUNT(*) > 1;

 open_time | price_type | count 
-----------+------------+-------
(0 rows)
-- Sonuç: Mükerrer kayıt sayısı = 0 (DOĞRULANDI)

-- 4. Negatif Kapanış Spread'i Kontrolü (ask.close < bid.close)
SELECT COUNT(*) 
FROM candles a 
JOIN candles b ON a.open_time = b.open_time 
  AND a.instrument_id = b.instrument_id 
  AND a.timeframe = b.timeframe 
  AND a.source_id = b.source_id 
  AND a.revision = b.revision
WHERE a.instrument_id = 'XAUUSD' 
  AND a.timeframe = 60 
  AND a.source_id = 'market_data_lab_m1' 
  AND a.revision = 0
  AND a.price_type = 'ASK' 
  AND b.price_type = 'BID' 
  AND a.close < b.close;

 count 
-------
     0
(1 row)
-- Sonuç: Negatif spread anormalliği = 0 (DOĞRULANDI)

-- 5. ASK-Only Kayıt Denetimi (BID karşılığı olmayan ASK kayıtları)
SELECT a.open_time, a.price_type, a.open, a.high, a.low, a.close 
FROM candles a 
LEFT JOIN candles b ON a.open_time = b.open_time 
  AND a.instrument_id = b.instrument_id 
  AND a.timeframe = b.timeframe 
  AND a.source_id = b.source_id 
  AND a.revision = b.revision 
  AND b.price_type = 'BID'
WHERE a.instrument_id = 'XAUUSD' 
  AND a.timeframe = 60 
  AND a.source_id = 'market_data_lab_m1' 
  AND a.revision = 0 
  AND a.price_type = 'ASK' 
  AND b.open_time IS NULL;

         open_time          | price_type |     open      |     high      |      low      |     close     
----------------------------+------------+---------------+---------------+---------------+---------------
 2026-08-16 23:59:00+00     | ASK        | 4370.84500000 | 4371.58500000 | 4370.62500000 | 4370.87500000
(1 row)
-- Sonuç: Tam olarak 1 adet ASK-only kayıt mevcuttur (2026-08-16 23:59:00 UTC) (DOĞRULANDI)

-- 6. BID-Only Kayıt Denetimi (ASK karşılığı olmayan BID kayıtları)
SELECT b.open_time, b.price_type, b.open, b.high, b.low, b.close 
FROM candles b 
LEFT JOIN candles a ON b.open_time = a.open_time 
  AND b.instrument_id = a.instrument_id 
  AND b.timeframe = a.timeframe 
  AND b.source_id = a.source_id 
  AND b.revision = a.revision 
  AND a.price_type = 'ASK'
WHERE b.instrument_id = 'XAUUSD' 
  AND b.timeframe = 60 
  AND b.source_id = 'market_data_lab_m1' 
  AND b.revision = 0 
  AND b.price_type = 'BID' 
  AND a.open_time IS NULL;

 open_time | price_type | open | high | low | close 
-----------+------------+------+------+-----+-------
(0 rows)
-- Sonuç: BID-only kayıt sayısı = 0 (DOĞRULANDI)
```

---

## 3. Madde 2: Ham CSV ile PostgreSQL OHLC Karşılaştırması

Market-Data-Lab deposundaki ham CSV dosyaları (`xauusd_bid_m1_*.csv` ve `xauusd_ask_m1_*.csv`) ile PostgreSQL'deki `candles` tablosu, kullanıcının talep ettiği 6 zorunlu senaryo ve ek seans sınırları dahil olmak üzere **12 farklı zaman damgasında (toplam 24 kotasyon)** birebir karşılaştırılmıştır:

### 3.1. Birebir Karşılaştırma Matrisi (12 Zaman Damgası / 24 Kotasyon)

| UTC Zaman Damgası | Fiyat Türü | Durum ve Senaryo Açıklaması | Ham CSV OHLC Değerleri | PostgreSQL DB OHLC Değerleri | Sayısal Eşleşme Durumu |
|---|---|---|---|---|---|
| `2024-01-08 12:00:00 UTC` | **BID** | Ocak 2024 Normal İşlem Günü (Londra Seansı) | `O=2019.958, H=2020.225, L=2019.525, C=2019.908` | `O=2019.958, H=2020.225, L=2019.525, C=2019.908` | ✅ **BİREBİR EŞLEŞTİ** |
| `2024-01-08 12:00:00 UTC` | **ASK** | Ocak 2024 Normal İşlem Günü (Londra Seansı) | `O=2020.255, H=2020.555, L=2019.845, C=2020.255` | `O=2020.255, H=2020.555, L=2019.845, C=2020.255` | ✅ **BİREBİR EŞLEŞTİ** |
| `2024-12-25 23:00:00 UTC` | **BID** | 25 Aralık 2024 Noel Akşamı İlk Açılış Barı | `O=2619.205, H=2619.255, L=2616.255, C=2616.255` | `O=2619.205, H=2619.255, L=2616.255, C=2616.255` | ✅ **BİREBİR EŞLEŞTİ** |
| `2024-12-25 23:00:00 UTC` | **ASK** | 25 Aralık 2024 Noel Akşamı İlk Açılış Barı | `O=2620.445, H=2621.095, L=2617.095, C=2617.195` | `O=2620.445, H=2621.095, L=2617.095, C=2617.195` | ✅ **BİREBİR EŞLEŞTİ** |
| `2024-12-25 23:47:00 UTC` | **BID** | 25 Aralık 2024 Noel Açık Seans Düşük Volatilite (Flat) | `O=2618.125, H=2618.125, L=2618.125, C=2618.125` | `O=2618.125, H=2618.125, L=2618.125, C=2618.125` | ✅ **BİREBİR EŞLEŞTİ** |
| `2024-12-25 23:47:00 UTC` | **ASK** | 25 Aralık 2024 Noel Açık Seans Düşük Volatilite (Flat) | `O=2618.982, H=2619.026, L=2618.982, C=2619.026` | `O=2618.982, H=2619.026, L=2618.982, C=2619.026` | ✅ **BİREBİR EŞLEŞTİ** |
| `2025-12-25 23:00:00 UTC` | **BID** | 25 Aralık 2025 Noel Akşamı İlk Açılış Barı | `O=4479.029, H=4479.029, L=4479.029, C=4479.029` | `O=4479.029, H=4479.029, L=4479.029, C=4479.029` | ✅ **BİREBİR EŞLEŞTİ** |
| `2025-12-25 23:00:00 UTC` | **ASK** | 25 Aralık 2025 Noel Akşamı İlk Açılış Barı | `O=4480.051, H=4480.051, L=4480.051, C=4480.051` | `O=4480.051, H=4480.051, L=4480.051, C=4480.051` | ✅ **BİREBİR EŞLEŞTİ** |
| `2024-06-12 14:30:00 UTC` | **BID** | 12 Haziran 2024 Normal İşlem Günü (ABD TÜFE Barı) | `O=2325.685, H=2325.715, L=2324.375, C=2324.375` | `O=2325.685, H=2325.715, L=2324.375, C=2324.375` | ✅ **BİREBİR EŞLEŞTİ** |
| `2024-06-12 14:30:00 UTC` | **ASK** | 12 Haziran 2024 Normal İşlem Günü (ABD TÜFE Barı) | `O=2326.122, H=2326.125, L=2324.772, C=2324.825` | `O=2326.122, H=2326.125, L=2324.772, C=2324.825` | ✅ **BİREBİR EŞLEŞTİ** |
| `2025-07-16 10:15:00 UTC` | **BID** | 16 Temmuz 2025 Normal İşlem Günü | `O=3337.765, H=3337.885, L=3337.295, C=3337.385` | `O=3337.765, H=3337.885, L=3337.295, C=3337.385` | ✅ **BİREBİR EŞLEŞTİ** |
| `2025-07-16 10:15:00 UTC` | **ASK** | 16 Temmuz 2025 Normal İşlem Günü | `O=3338.275, H=3338.395, L=3337.795, C=3337.865` | `O=3338.275, H=3338.395, L=3337.795, C=3337.865` | ✅ **BİREBİR EŞLEŞTİ** |
| `2026-04-15 13:45:00 UTC` | **BID** | 15 Nisan 2026 Normal İşlem Günü | `O=4820.325, H=4822.565, L=4819.785, C=4822.565` | `O=4820.325, H=4822.565, L=4819.785, C=4822.565` | ✅ **BİREBİR EŞLEŞTİ** |
| `2026-04-15 13:45:00 UTC` | **ASK** | 15 Nisan 2026 Normal İşlem Günü | `O=4820.955, H=4823.185, L=4820.395, C=4823.185` | `O=4820.955, H=4823.185, L=4820.395, C=4823.185` | ✅ **BİREBİR EŞLEŞTİ** |
| `2026-08-16 23:59:00 UTC` | **BID** | 16 Ağustos 2026 Tek Taraflı (ASK-only) Kotasyon | `YOK (Ham CSV'de Bulunmuyor)` | `YOK (DB'de Bulunmuyor)` | ✅ **BİREBİR EŞLEŞTİ (Her İkisinde de Yok)** |
| `2026-08-16 23:59:00 UTC` | **ASK** | 16 Ağustos 2026 Tek Taraflı (ASK-only) Kotasyon | `O=4370.845, H=4371.585, L=4370.625, C=4370.875` | `O=4370.845, H=4371.585, L=4370.625, C=4370.875` | ✅ **BİREBİR EŞLEŞTİ** |
| `2026-09-07 18:28:00 UTC` | **BID** | 7 Eylül 2026 ABD Labor Day Erken Kapanış Son Barı | `O=4403.635, H=4404.915, L=4403.395, C=4404.775` | `O=4403.635, H=4404.915, L=4403.395, C=4404.775` | ✅ **BİREBİR EŞLEŞTİ** |
| `2026-09-07 18:28:00 UTC` | **ASK** | 7 Eylül 2026 ABD Labor Day Erken Kapanış Son Barı | `O=4405.985, H=4407.265, L=4405.745, C=4407.125` | `O=4405.985, H=4407.265, L=4405.745, C=4407.125` | ✅ **BİREBİR EŞLEŞTİ** |
| `2026-09-30 22:27:00 UTC` | **BID** | 30 Eylül 2026 Seans Boşluğu Öncesi Bar | `O=4155.595, H=4155.705, L=4155.585, C=4155.585` | `O=4155.595, H=4155.705, L=4155.585, C=4155.585` | ✅ **BİREBİR EŞLEŞTİ** |
| `2026-09-30 22:27:00 UTC` | **ASK** | 30 Eylül 2026 Seans Boşluğu Öncesi Bar | `O=4156.425, H=4156.535, L=4156.415, C=4156.415` | `O=4156.425, H=4156.535, L=4156.415, C=4156.415` | ✅ **BİREBİR EŞLEŞTİ** |
| `2026-09-30 22:29:00 UTC` | **BID** | 30 Eylül 2026 Seans Boşluğu Sonrası Bar | `O=4155.695, H=4156.365, L=4155.555, C=4155.765` | `O=4155.695, H=4156.365, L=4155.555, C=4155.765` | ✅ **BİREBİR EŞLEŞTİ** |
| `2026-09-30 22:29:00 UTC` | **ASK** | 30 Eylül 2026 Seans Boşluğu Sonrası Bar | `O=4156.525, H=4157.185, L=4156.385, C=4156.595` | `O=4156.525, H=4157.185, L=4156.385, C=4156.595` | ✅ **BİREBİR EŞLEŞTİ** |
| `2026-10-19 13:59:00 UTC` | **BID** | 19 Ekim 2026 Arşiv Bitiş Sınırı (En Son Bar) | `O=4153.865, H=4155.035, L=4153.235, C=4154.495` | `O=4153.865, H=4155.035, L=4153.235, C=4154.495` | ✅ **BİREBİR EŞLEŞTİ** |
| `2026-10-19 13:59:00 UTC` | **ASK** | 19 Ekim 2026 Arşiv Bitiş Sınırı (En Son Bar) | `O=4154.376, H=4155.555, L=4153.727, C=4155.079` | `O=4154.376, H=4155.555, L=4153.727, C=4155.079` | ✅ **BİREBİR EŞLEŞTİ** |

**Kabul Kriteri Karşılandı:** Kaynak CSV ile PostgreSQL OHLC değerleri sayısal olarak **birebir (%100)** eşleşmektedir. Uyuşmazlık sayısı: **0**.

---

## 4. Madde 3: Sentetik Kayıt İstatistiklerinin Düzenlenmesi

Kullanıcı direktifine uygun olarak, iki yönlü sentetik ayıklama toplamları rapora net biçimde yansıtılmıştır:

* **Elenen Sentetik BID:** **423.270**
* **Elenen Sentetik ASK:** **423.270**
* **TOPLAM ELENEN SENTETİK KAYIT:** **846.540**

Ham dosyalardaki 2.851.365 satırın (1.425.682 BID + 1.425.683 ASK) %29,69'una karşılık gelen 846.540 adet yapay carry-forward barı filtrelenmiş; geriye kalan **2.004.825 adet saf aktif piyasa kotasyonu** veritabanına yazılmıştır.

---

## 5. Madde 4: Pytest Tam Test Paketi Doğrulaması (327/327 Test — %100 Başarı)

Tüm veri aktarımı, bitemporal tablo güncellemeleri ve matematiksel kısıt denetimlerinin ardından 327 testin tamamı tek komutla çalıştırılmıştır:

```powershell
(.venv) PS C:\trader\traderLab\traderlab> python -m pytest -q
........................................................................ [ 22%]
........................................................................ [ 44%]
........................................................................ [ 66%]
........................................................................ [ 88%]
.......................................                                  [100%]
327 passed in 136.50s (0:02:16)
```

---

## 6. Nihai Kapanış Beyanı

* **Faz 1B-C5B:** Grafik ve gezinme modülü başarıyla teslim edilmiş ve kapatılmıştır.
* **Faz 1C:** 34 aylık yerel Dukascopy XAUUSD M1 arşivi başarıyla filtrelenmiş; **1.002.412 BID** ve **1.002.413 ASK** olmak üzere **toplam 2.004.825 temiz M1 kotasyonu** sıfır hata, sıfır sentetik kirlilik ve sıfır anormallikle PostgreSQL veritabanına yazılmıştır.
* Doğrudan SQL aggregate sorguları ve 12 zaman damgalı ham CSV vs PostgreSQL OHLC denetimi sıfır uyuşmazlıkla tamamlanmıştır.
* **Faz 1C Durumu:** **`ACCEPTED — CLOSED`** olarak tescil edilmiştir.
* Sistem güvenli biçimde durdurulmuş olup, **Faz 2 (Özellik Mühendisliği / Feature Engineering & Strateji Geliştirme)** aşamasına tamamen hazırdır.
