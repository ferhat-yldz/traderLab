# TRADERLAB — FAZ 1B-B2A TEKNİK DOĞRULAMA VE PİLOT RAPORU
## GitHub Market-Data-Lab Deposunun Ayrı Klonlanması, TRADERLAB Bağlantısı ve XAUUSD CSV Pilot Doğrulaması

**Tarih:** 2026-10-20  
**Mühendislik Rolü:** Principal Python Software Architect, Senior Quantitative Data Engineer & Git Repository Integration Engineer  
**Kapsam:** Kardeş Dizin Ayrımı, Git Partial Clone + Sparse-Checkout, Salt Okunur Yol Çözümleme, CSV Teknik & Mikro-Yapı Doğrulaması, Point-in-Time Sınıflandırması ve Test Paketi  
**Nihai Karar / Durum:** **`READY_WITH_LIMITATIONS`**  
*(Altyapı ayrımı, sparse-checkout, salt-okunur konfigürasyon, CSV şema ve veri doğrulaması %100 başarılı; lisanssızlık ve üçüncü taraf veri kökeni sebebiyle RESEARCH_ESTIMATED statüsünde kullanım kısıtlamalıdır.)*

---

## 1. YÖNETİCİ ÖZETİ

TRADERLAB Faz 1B-B2A kapsamında, Faz 1B-B1'de tespit edilen doğrudan Dukascopy HTTP blokajı sonrasında alternatif tarihsel veri kaynağı adayı olarak incelenen `https://github.com/kevingtlin/Market-Data-Lab` GitHub deposu değerlendirilmiştir.

Temel hedefler:
1. TRADERLAB altyapısını bozmadan ve Git geçmişlerini birleştirmeden depoyu bağımsız bir kardeş klasöre (`C:\trader\traderLab\Market-Data-Lab`) yerleştirmek.
2. 2010–2026 arasındaki ~850 MiB büyüklüğündeki tüm arşivi indirmek yerine **Git Partial Clone (`--filter=blob:none`) + Sparse-Checkout (`--no-cone`)** kullanarak yalnızca Ocak 2024 M1 BID ve ASK dosyaları ile belgeleri indirmek (~4.56 MB).
3. TRADERLAB altyapı katmanında (`traderlab.infrastructure.providers.market_data_lab`) salt okunur, domain katmanından kesinlikle yalıtılmış bir adaptör kurmak.
4. Ocak 2024 CSV verilerinin şema, UTC milisaniye, M1 zaman hizalaması, monotonluk, OHLC tutarlılığı, spread ve flat-bar (piyasa kapalı dönem) karakteristiğini teknik olarak denetlemek.
5. Veriyi bağımsız üçüncü taraf araştırma verisi olarak sınıflandırmak; resmi Dukascopy lisansı veya doğrudan veri garantisi vermemek.

### Temel Doğrulama Özeti:
- **Git ve Dizin İzolasyonu:** Kardeş dizin yapısı korunmuş, Git submodule veya remote birleşimi yapılmamış, iki projenin bağımsızlığı eksiksiz sağlanmıştır.
- **Aktarım Boyutu Tasarrufu:** Sparse-checkout ile yalnızca 6 dosya (2 CSV + 4 doküman = ~4.56 MB) indirilmiş, 850+ MiB gereksiz ağ trafiği engellenmiştir.
- **CSV Teknik Kalitesi:** Ocak 2024 için 44.640 dakikanın tamamı ($31 \times 1440$) eksiksiz mevcuttur. Zaman damgası monotonik ve 60.000 ms adımlarla hizalıdır. OHLC kural ihlali yoktur. Negatif fiyat veya negatif spread tespit edilmemiştir.
- **Piyasa Kapalılığı (Flat Bars):** 44.640 satırın 14.397'si (~%32,25) $O=H=L=C$ flat bar durumundadır. Bu satırlar hafta sonları, yılbaşı tatili ve günlük 1 saatlik rollover aralıklarında `dukascopy-node` aracının son fiyatı taşımasından (carry-forward) kaynaklanmaktadır.
- **Test ve Tip Güvenliği:** 30 yeni birim ve pilot entegrasyon testi eklenmiş; toplam test sayısı **195'ten 226'ya yükselmiştir (%100 geçiş, 1.86 saniye)**. `ruff check`, `ruff format` ve `mypy src/traderlab/` sıfır hata ile tamamlanmıştır.

---

## 2. DİZİN VE GİT DEPO MİMARİSİ

### A. Gerçek Dizin Düzeni

```text
C:\trader\traderLab\
│
├── traderlab\                                   ← Ana Araştırma Projemiz (İzole)
│   ├── .env                                     (EXTERNAL_XAUUSD_DATA_DIR=../Market-Data-Lab)
│   ├── .env.example
│   ├── src\traderlab\
│   │   ├── core\
│   │   ├── domain\                              (Sıfır harici dosya/git bağımlılığı)
│   │   └── infrastructure\
│   │       └── providers\
│   │           ├── dukascopy\
│   │           └── market_data_lab\             ← Salt okunur adaptör & validator
│   ├── tests\
│   │   └── test_market_data_lab.py              (30 yeni test)
│   └── docs\reports\phase-1b-b2a.md
│
└── Market-Data-Lab\                             ← Ayrı Klonlanmış Kardeş Dizin (İzole Git)
    ├── .git\                                    (Sparse-checkout & blob-filter aktif)
    ├── README.md
    ├── XAUUSD_DUKASCOPY_M1_BID_ASK_README.md
    ├── registry\
    │   └── datasets.yaml
    ├── datasets\
    │   └── XAUUSD-DUKASCOPY-M1-BID-ASK\
    │       └── metadata.yaml
    └── xauusd\
        ├── bid\m1\
        │   └── xauusd_bid_m1_2024_01.csv
        └── ask\m1\
            └── xauusd_ask_m1_2024_01.csv
```

### B. Git Depo ve İzolasyon Kimliği

| Özellik | Değer | Notlar |
| :--- | :--- | :--- |
| **Klonlanan Depo URL** | `https://github.com/kevingtlin/Market-Data-Lab.git` | GitHub public deposu |
| **Commit SHA** | `78fd60f8bf3a1cebe198828147e54e4ed32385d8` | Klonlama anındaki en son commit |
| **Branch** | `main` | Varsayılan dal |
| **Git Clone Filtresi** | `--filter=blob:none --no-checkout` | Partial clone (ağ tasarrufu) |
| **Sparse Checkout Modu** | `git sparse-checkout set --no-cone` | Yalnızca belirtilen 6 dosya |
| **Git Submodule?** | **HAYIR** | İki repo tamamen bağımsız |
| **Git Remote Birleşimi?** | **HAYIR** | Hiçbir git remote karıştırılmadı |
| **Çalıştırılabilir Kod?** | **HAYIR** | Dış depodaki hiçbir script yürütülmedi |

---

## 3. İNDİRİLEN DOSYALAR, BOYUTLAR VE SHA-256 DOĞRULAMASI

Sparse-checkout ile indirilen tüm dosyalar ve kriptografik özetleri:

| Bağıl Dosya Yolu | Boyut (Bayt) | SHA-256 Checksum |
| :--- | :---: | :--- |
| `README.md` | 1.086 | `176e335b304c7f29268452515c2cc6b614a7f27acfa3fb38dccf64b5e28defe6` |
| `XAUUSD_DUKASCOPY_M1_BID_ASK_README.md` | 6.157 | `d5d7cc2fbce1b575a60f5b8a69ff5831d319cc6aee5992fdde2a4f048af96e49` |
| `registry/datasets.yaml` | 899 | `410e2155dba7bcf35182b1dddb4e17653ab9c8d5093e728f0676ee972be6bca4` |
| `datasets/XAUUSD-DUKASCOPY-M1-BID-ASK/metadata.yaml` | 1.113 | `1dea12ac8a8d6c02d9685016abd034f4d216048737b108b1209acbab32aefd34` |
| `xauusd/bid/m1/xauusd_bid_m1_2024_01.csv` | 2.276.634 | `6fa24d76649fec4f15ccf6b7324a1ea8f1ed420c71438e41129d9a7203d68797` |
| `xauusd/ask/m1/xauusd_ask_m1_2024_01.csv` | 2.276.587 | `25139c9795be9370a272c650d05b100d92604d016b8730084530d448bfb34612` |

**Toplam Disk Alanı:** 4.562.476 bayt (~4,35 MiB).

---

## 4. LİSANS, VERİ KÖKENİ (LINEAGE) VE HUKUKİ KISITLAR

1. **Beyan Edilen Kaynak:** Depo dokümantasyonu (`metadata.yaml`), verilerin `dukascopy-node@1.46.4` aracıyla Dukascopy sunucularından indirildiğini beyan etmektedir.
2. **Depo İçi Lisans:** Depo kökünde veya alt dizinlerinde **resmî bir LICENSE / COPYING belgesi BULUNMAMAKTADIR.**
3. **Fikri Mülkiyet ve Yeniden Dağıtım Durumu:**  
   GitHub üzerinde bir deponun "public" olması, içindeki finansal piyasa verilerinin serbestçe ticari amaçla kullanılabileceği veya üçüncü taraflara dağıtılabileceği anlamına gelmez.
4. **Hukuki Statü Kararı:**  
   - Bu veri seti TRADERLAB bünyesinde **"Doğrulanmamış Üçüncü Taraf Araştırma Verisi" (Unverified Third-Party Research Data)** statüsünde tutulacaktır.
   - Doğrudan Dukascopy'den alınmış resmî ve garantili veri statüsü **verilmemiştir**.
   - Ticari kullanım, dağıtım ve gerçek para ile çalışan otomatik trading amacıyla onaylanmış **sayılamaz**.
   - Yalnızca **akademik/araştırma amaçlı offline simülasyon ve kod algoritması doğrulamasında** kısıtlı olarak kullanılabilir.

---

## 5. TRADERLAB HARİCİ KLASÖR BAĞLANTISI VE YAPILANDIRMA

Clean Architecture kurallarına tam uyum sağlanmıştır:
- **Domain Katmanı Bağımsızlığı:** `traderlab.domain` katmanında hiçbir dosya yolu, dosya sistemi veya git referansı bulunmaz.
- **Altyapı Ayarları:** `src/traderlab/infrastructure/providers/market_data_lab/config.py` modülü `MarketDataLabSettings` sınıfını barındırır.
- **Konfigürasyon Değişkeni:** `EXTERNAL_XAUUSD_DATA_DIR` varsayılan olarak `../Market-Data-Lab` değerine ayarlanmıştır.
- **Pathlib Çözümleme:** Göreli yollar `TRADERLAB_PROJECT_ROOT` (yani `C:\trader\traderLab\traderlab`) taban alınarak çözümlenir ve `C:\trader\traderLab\Market-Data-Lab` hedefine yönlenir.
- **Hata Yönetimi:**
  - Dizin bulunamazsa: `ExternalDirectoryNotFoundError`
  - BID veya ASK CSV dosyası eksikse: `MissingDatasetFileError`
  - Başlık veya sütun bozuksa: `InvalidCsvSchemaError`
  - Fiyat veya OHLC bozuksa: `InvalidCandleDataError`
  - Zaman damgası veya sıralama bozuksa: `TimestampAlignmentError`
  - ASK < BID negatif spread oluşursa: `NegativeSpreadError`
- **Salt Okunur Güvenlik:** Dosyalar `open(..., "r", encoding="utf-8")` ile yalnızca okuma modunda açılır. Hiçbir yazma, dosya güncelleme veya otomatik `git pull` işlemi bulunmamaktadır.
- **Ortam Dosyaları:** `.env` ve `.env.example` dosyalarına `EXTERNAL_XAUUSD_DATA_DIR=../Market-Data-Lab` eklenmiştir.

---

## 6. OCAK 2024 CSV TEKNİK VE MİKRO-YAPI DOĞRULAMA BULGULARI

Ocak 2024 dönemi için indirilen `xauusd_bid_m1_2024_01.csv` ve `xauusd_ask_m1_2024_01.csv` dosyaları üzerinde yürütülen teknik denetim sonuçları:

| Doğrulama Kriteri | Beklenen | Gerçekleşen | Durum |
| :--- | :--- | :--- | :---: |
| **CSV Başlık Şeması** | `timestamp,open,high,low,close` | `timestamp,open,high,low,close` | **GEÇTİ** |
| **Sütun Sayısı** | 5 sütun | 5 sütun | **GEÇTİ** |
| **Toplam Satır Sayısı** | 44.640 veri satırı ($31 \text{ gün} \times 1440 \text{ dk}$) | 44.640 veri satırı | **GEÇTİ** |
| **Timestamp Birimi** | Unix epoch milisaniye (UTC) | 13 haneli tam sayı milisaniye | **GEÇTİ** |
| **İlk Zaman Damgası** | 1704067200000 (2024-01-01 00:00:00 UTC) | 1704067200000 (2024-01-01 00:00:00 UTC) | **GEÇTİ** |
| **Son Zaman Damgası** | 1706745540000 (2024-01-31 23:59:00 UTC) | 1706745540000 (2024-01-31 23:59:00 UTC) | **GEÇTİ** |
| **M1 Zaman Hizalaması** | `ts % 60000 == 0` (Tam dakika başı) | Sıfır artık (tüm satırlarda tam dakika) | **GEÇTİ** |
| **Monotonik Artış** | `ts[i] == ts[i-1] + 60000` | 44.639 adımın tamamında tam 60.000 ms artış | **GEÇTİ** |
| **Tekrarlanan Zaman Damgası** | 0 adet | 0 adet | **GEÇTİ** |
| **OHLC Tutarlılığı** | $H \ge L$, $H \ge O$, $H \ge C$, $L \le O$, $L \le C$ | Sıfır ihlal | **GEÇTİ** |
| **Pozitif Fiyat Denetimi** | Tüm fiyatlar $> 0$ | Min: $2001.800 USD, Max: $2088.472 USD | **GEÇTİ** |
| **Decimal Hassasiyeti** | 3 ondalık basamak (ör. 2062.688) | String'den doğrudan Decimal dönüşümü | **GEÇTİ** |
| **BID/ASK Eşleşmesi** | %100 zaman damgası örtüşmesi | 44.640 / 44.640 (%100 senkron) | **GEÇTİ** |
| **Negatif Spread Denetimi** | $C_{ask} \ge C_{bid}$ | Sıfır negatif spread | **GEÇTİ** |
| **Min / Max / Ort Spread** | Pozitif ve makul spread aralığı | Min: $0.085, Max: $2.490, Ort: $0.4677 | **GEÇTİ** |

### Örnek Mum Eşleşmesi (2024-01-16 12:00:00 UTC):
- **BID Bar:** `O=2039.775, H=2039.935, L=2039.005, C=2039.565`
- **ASK Bar:** `O=2040.125, H=2040.275, L=2039.345, C=2039.922`
- **Kapanış Spread'i:** $2039.922 - 2039.565 = \$0.357$ (35.7 sent).
- **Önemli İlke Hatırlatması:** Her iki mumun zaman damgası aynı olsa dahi, High ve Low uç noktalarının dakikanın aynı saniyesinde oluştuğu varsayılmamıştır. Spread hesaplaması yalnızca senkronize kapanış fiyatları üzerinden yürütülmüştür.

### Flat Bar (Piyasa Kapalı / Carry-Forward) Analizi:
- Toplam 44.640 satır içerisinde **14.397 adet flat bar** ($O=H=L=C$) tespit edilmiştir (oran: **%32,25**).
- **Dağılım Nedenleri:**
  - 1 Ocak 2024 Yılbaşı tatili (tam gün piyasa kapalı).
  - Hafta sonları: 6-7 Ocak, 13-14 Ocak, 20-21 Ocak, 27-28 Ocak.
  - Hafta içi her gün New York kapanışı ile Sydney açılışı arasındaki 1 saatlik piyasa rollover aralığı (22:00–23:00 UTC).
- **Veri Yorumu:** İndirici araç (`dukascopy-node`), piyasa kapalıyken eksik dakika bırakmak yerine son fiyatı bir sonraki seans açılışına kadar taşıyarak (carry-forward) düz çizgiler üretmiştir. Bu satırlar bozuk veri değildir; ancak TRADERLAB'a aktarılırken `is_synthetic = True` olarak işaretlenmeli ve backtest motorunda bu barlarda işlem açılmamalıdır.

---

## 7. POINT-IN-TIME (PIT) VE VERİ KÖKENİ POLİTİKASI

1. **Gözlemlenebilirlik / Available-At Gerçeği:**  
   CSV dosyalarında satır bazında gerçek `published_at` veya `received_at` bilgisi bulunmamaktadır. Bu nedenle TRADERLAB domain `Candle` nesnesine dönüştürülürken bu alanlar `None` olarak bırakılmıştır; **sahte zaman damgası uydurulmamıştır**.
2. **Karar Zamanı Ayrımı:**  
   GitHub deposunun commit tarihi (Ekim 2026), 2024 yılındaki tarihsel karar anı olarak kullanılamaz.
3. **PIT Sınıflandırması:**  
   - `price_type`: Açıkça `PriceType.BID` veya `PriceType.ASK`.
   - `availability_confidence`: `AvailabilityConfidence.UNKNOWN`.
   - `STRICT_PIT`: **Uygun değildir.** Gerçek zamanlı yayın gecikmesi bilinmemektedir.
   - `RESEARCH_ESTIMATED`: Araştırma ve kaba backtest simülasyonları için sınırları açıkça belgelenerek kullanılabilir.
4. **Tekrarlanabilirlik:**  
   Veri seti Git commit SHA (`78fd60f8bf3a1cebe198828147e54e4ed32385d8`) ve dosya SHA-256 değerleri ile sabitlenmiştir.

---

## 8. TEST VE KALİTE DOĞRULAMA SONUÇLARI

### A. Eklenen ve Güncellenen Dosyalar

| Dosya Yolu | Tür | Açıklama |
| :--- | :--- | :--- |
| `src/traderlab/infrastructure/providers/market_data_lab/errors.py` | Yeni | Modüle özgü hata tipleri |
| `src/traderlab/infrastructure/providers/market_data_lab/config.py` | Yeni | Pydantic BaseSettings ve yol çözümleyici |
| `src/traderlab/infrastructure/providers/market_data_lab/csv_reader.py` | Yeni | M1 CSV okuyucu, validator, spread eşleyici |
| `src/traderlab/infrastructure/providers/market_data_lab/__init__.py` | Yeni | Temiz dışa aktarım arayüzü |
| `tests/test_market_data_lab.py` | Yeni | 30 kapsamlı test (sentetik + gerçek pilot) |
| `tests/test_smoke.py` | Güncellendi | Import smoke testine eklendi |
| `.env` ve `.env.example` | Güncellendi | `EXTERNAL_XAUUSD_DATA_DIR` eklendi |
| `docs/reports/phase-1b-b2a.md` | Yeni | Faz 1B-B2A resmi teslim raporu |

### B. Test Sonuçları Özeti

| Test Paketi | Başlangıç (Faz 1B-B1) | Bitiş (Faz 1B-B2A) | Değişim | Sonuç |
| :--- | :---: | :---: | :---: | :---: |
| **Toplam Test Sayısı** | **195** | **226** | **+31** | **%100 PASSED** |
| `test_market_data_lab.py` | 0 | 30 | +30 | %100 PASSED |
| `test_smoke.py` | 38 | 39 | +1 | %100 PASSED |
| `test_dukascopy_decoder.py` | 26 | 26 | 0 | %100 PASSED |
| `test_candle_pit_forex.py` | 64 | 64 | 0 | %100 PASSED |
| `test_domain_ports.py` | 24 | 24 | 0 | %100 PASSED |
| PostgreSQL Entegrasyon Testleri | 11 | 11 | 0 | %100 PASSED |
| Diğer Birim Testleri | 32 | 32 | 0 | %100 PASSED |

### C. Statik Kod Analizi ve Linter Doğrulaması

```bash
# 1. Test Çalıştırma
python -m pytest -v --tb=short
# Çıktı: 226 passed in 1.86s

# 2. Test Koleksiyonu
python -m pytest --collect-only -q
# Çıktı: 226 tests collected in 0.53s

# 3. Ruff Linter
ruff check src/ tests/
# Çıktı: All checks passed!

# 4. Ruff Biçimlendirme
ruff format --check src/ tests/
# Çıktı: 67 files already formatted

# 5. Mypy Tip Kontrolü
mypy src/traderlab/
# Çıktı: Success: no issues found in 50 source files
```

---

## 9. AÇIK KALAN PROBLEMLER VE RİSKLER

1. **Hukuki Lisans Belirsizliği:**  
   GitHub deposunda resmî bir veri lisansı veya Dukascopy yeniden dağıtım izni bulunmamaktadır. Veri yalnızca araştırma/kod geliştirme referansı olarak ele alınmalıdır.
2. **Piyasa Kapalı Dönemlerindeki Sentetik Düz Çizgiler (Flat Bars):**  
   Ocak 2024 verisinin %32,25'i tatil ve rollover saatlerinde taşınan flat barlardan oluşmaktadır. İleride M5/H1 türetimi veya indikatör hesaplaması yapılırken bu barlar filtrelenmeli veya `is_synthetic` bayrağı ile dışlanmalıdır.
3. **İntraminute (Dakika İçi) Fiyat Sıralaması Bilinmemektedir:**  
   M1 barlarında High ve Low seviyelerinden hangisinin önce test edildiği bilinemez. Stop-loss ve take-profit seviyelerinin aynı barda vurulması durumunda muhafazakar varsayımlar uygulanmalıdır.
4. **Veri Hacmi (Volume) Bilgisi Yoktur:**  
   Dukascopy CSV formatında `volume` sütunu bulunmamaktadır (`volume = None`). Hacim tabanlı analizler bu veri setiyle yapılamaz.

---

## 10. FAZ 1B-B2B İÇİN TEKNİK TAVSİYELER

Kullanıcı onayı verilmesi durumunda bir sonraki faz olan **Faz 1B-B2B** için önerilen yol haritası:

1. **Büyük Ölçekli Toplu İndirme Yapılmamalı:**  
   2010–2026 arasındaki tüm veri seti yerine, araştırma için öncelikli olan yakın dönem (ör. 2024–2026 arası veya seçilecek belirli çeyrekler) aşamalı sparse-checkout ile getirilmelidir.
2. **PostgreSQL Toplu Yükleme (Batch Ingestion) Mimarisi:**  
   Tek tek satır ekleme yerine `COPY` veya bulk insert optimizasyonu kurgulanmalı; idempotency (`ON CONFLICT DO NOTHING / UPDATE`) korunmalıdır.
3. **Session Filter Adaptörü:**  
   Hafta sonu ve tatil flat barlarını filtreleyen bir `TradingSessionFilter` geliştirilmelidir.
4. **M1'den Üst Zaman Dilimlerine (M5, M15, H1) Agregasyon Motoru:**  
   Doğrulanmış M1 BID ve ASK barlarından türetilmiş üst zaman dilimleri matematiksel olarak üretilmeli ve doğrulanmalıdır.

---

## 11. NİHAİ KARAR VE BİLDİRİM

```text
================================================================================
KABUL KARARI: READY_WITH_LIMITATIONS
================================================================================
- Git Kardeş Dizin Ayrımı ve Sparse-Checkout: HAZIR VE ÇALIŞIYOR (6 dosya, ~4.56 MB)
- TRADERLAB Salt Okunur Harici Konfigürasyon: HAZIR VE DOĞRULANDI
- CSV Şema, Timestamp ve Finansal OHLC Doğrulaması: %100 BAŞARILI
- 30 Yeni Test Eklendi, Toplam 226 Test: %100 GEÇTİ
- Linter, Format ve Tip Denetimleri: SIFIR HATA (ruff check, ruff format, mypy)
- KISITLAMA: Veri seti üçüncü taraf araştırma verisi niteliğindedir (RESEARCH_ESTIMATED).
  Ticari dağıtım lisansı yoktur; flat barlar (~%32) seans dışı filtreleme gerektirir.
================================================================================
```

*Görev tamamlanmıştır. Kullanıcı onayı alınmadan Faz 1B-B2B'ye geçilmeyecektir.*
