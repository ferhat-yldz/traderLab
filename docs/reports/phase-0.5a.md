# TRADERLAB — FAZ 0.5A Teslim ve Mühendislik Raporu
## Forex Fiyat Modeli, Point-in-Time ve Bitemporal Domain Güvenilirliği

**Tarih:** 2024-10-19  
**Aşama:** FAZ 0.5A  
**Rol:** Senior Python Software Architect, Quantitative Trading Systems Engineer & Financial Data Engineer  
**Durum:** Tamamlandı (Başarılı)

---

### 1. Başlangıç Durumu

Faz 0 bağımsız mimari denetiminde tespit edilen kritik bulgular:
- `Candle` modelinde BID, ASK, MID ayrımı yoktu; fiyat tipi örtük ve belirsizdi.
- `mid_price` formülü `(high + low) / 2` olarak verilmişti; bu değer piyasa bid/ask spread midpoint'i değil, çubuğun geometrik aralık orta noktasıydı.
- Spread zamanla değişen bir mikro-yapı özelliği olmasına rağmen modelde temsil edilmiyordu.
- Point-in-Time (PIT) ve bitemporal zaman ayrımı eksikti; barlar yalnızca `open_time` ve `close_time` tutuyordu. Gerçek zamanlı gelen veri ile geçmiş toplu indirme (backfill) arasındaki erişilebilirlik farkı ayırt edilemiyordu.
- Tamamlanmamış (oluşmakta olan) barların geçmiş analizlerde kapalı gibi kullanılmasını engelleyen karar-zamanı kuralı eksikti.
- Zaman dilimi doğrulaması `tzinfo != UTC` şeklinde kırılgan bir eşitlik kontrolü içeriyordu.
- Başlangıç test sayısı: **94 test** (tamamı başarılı).

---

### 2. Değiştirilen ve Eklenen Dosyalar

| Dosya Yolu | İşlem Türü | Açıklama |
| :--- | :---: | :--- |
| `src/traderlab/core/types.py` | Değiştirildi | `PriceType`, `AvailabilityConfidence` enumları ve `ensure_utc` normalizasyon fonksiyonu eklendi. |
| `src/traderlab/domain/instruments/candle.py` | Değiştirildi | `Candle` modeli bitemporal zaman alanları, explicit `PriceType`, `spread`, `range_midpoint`, `is_synthetic`, `revision`, `allow_negative_prices` ve `is_available_at` ile güncellendi. |
| `src/traderlab/domain/ports.py` | Değiştirildi | `CandleRepository.get_candles` portuna `price_type` filtresi ve bitemporal `as_of_utc` sözleşme dokümantasyonu eklendi. |
| `tests/conftest.py` | Değiştirildi | `sample_candle` fikstürü explicit `price_type=PriceType.BID` ile güncellendi. |
| `tests/test_core_types.py` | Değiştirildi | `PriceType`, `AvailabilityConfidence` ve `ensure_utc` için birim testler eklendi. |
| `tests/test_domain_models.py` | Değiştirildi | Mevcut Candle testleri explicit `price_type=PriceType.BID` ve `range_midpoint` doğrulamalarıyla güncellendi. |
| `tests/test_candle_pit_forex.py` | **Yeni Eklendi** | Faz 0.5A gereksinimlerinin tamamını doğrulayan 25 kapsamlı birim test dosyası oluşturuldu. |
| `docs/decisions/adr-log.md` | Değiştirildi | ADR-006 (Explicit Price Types & Range Midpoint) ve ADR-007 (Six-Timestamp Bitemporal Model) mimari kayıtları eklendi. |
| `docs/reports/phase-0.5a.md` | **Yeni Eklendi** | Bu rapor oluşturuldu. |

---

### 3. Eklenen ve Değiştirilen Sınıflar & Fonksiyonlar

#### A. `traderlab.core.types`
1. **`PriceType(enum.Enum)`:**
   - Değerler: `BID = "BID"`, `ASK = "ASK"`, `MID = "MID"`, `UNKNOWN = "UNKNOWN"`.
   - Amaç: Forex ve OTC piyasalarında teklif (bid), talep (ask) ve orta (mid) akışlarını explicit hale getirir.
2. **`AvailabilityConfidence(enum.Enum)`:**
   - Değerler: `VERIFIED = "VERIFIED"`, `ESTIMATED = "ESTIMATED"`, `UNKNOWN = "UNKNOWN"`.
   - Amaç: Barın karar anında erişilebilir olduğuna dair güven düzeyini modeller (gerçek zamanlı ağ makbuzu vs. geçmiş veri).
3. **`ensure_utc(dt: datetime) -> datetime`:**
   - UTC-aware ve sıfır offset (`offset == 0`) denetimi yapar; `ZoneInfo("UTC")` veya `timezone(timedelta(0))` nesnelerini kanonik `datetime.UTC`'ye normalize eder; naive datetime nesnelerini reddeder.

#### B. `traderlab.domain.instruments.candle.Candle`
- **Yeni Alanlar:**
  - `price_type: PriceType` (Zorunlu, varsayılanı yoktur; çağrıcı açıkça belirtmelidir).
  - `published_at: datetime | None = None` (Sağlayıcı yayın zamanı).
  - `received_at: datetime | None = None` (Sistem ağ geçidi varış zamanı).
  - `available_at: datetime | None = None` (Karar mekanizması için doğrulanmış erişim anı).
  - `ingested_at: datetime = field(default_factory=utc_now)` (Veritabanı kalıcılık kayıt anı).
  - `availability_confidence: AvailabilityConfidence = AvailabilityConfidence.UNKNOWN` (Erişim güven derecesi).
  - `spread: Decimal | None = None` (Zamanla değişen spread; bilinmiyorsa `None`, ASLA sıfır varsayılmaz).
  - `is_synthetic: bool = False` (Sentetik/türetilmiş veri ayrımı).
  - `revision: int = 0` (Düzeltme ve sürüm sayacı).
  - `allow_negative_prices: bool = False` (XAUUSD ve EURUSD için pozitif fiyat zorunluluğu, emtia için esneklik).
- **Yeni Metot ve Nitelikler:**
  - `range_midpoint -> Decimal`: `(high + low) / 2` (Geometrik aralık orta noktası; gerçek bid/ask mid değildir).
  - `mid_price -> Decimal`: Geriye uyumluluk takma adı (alias).
  - `is_available_at(as_of: datetime) -> bool`: Karar zamanı PIT denetim metodu (`is_closed=False` durumunda kapalı analiz için reddeder; `available_at` veya `close_time` ile karşılaştırır).

---

### 4. Giderilen Mimari Sorunlar

1. **Örtük Fiyat Tipi Yanılsaması Giderildi:** Candle oluşturulurken `price_type` belirtilmesi zorunlu kılındı. Otomatik olarak BID varsayma engellendi (`TypeError`).
2. **Kavramsal Midpoint Hatası Düzeltildi:** `(high + low) / 2` ifadesinin gerçek bid/ask midpoint'i olmadığı mimari olarak netleştirildi ve `range_midpoint` olarak adlandırıldı.
3. **Sıfır Spread Varsayımı Engellendi:** Spread bilinmediğinde (`None`), algoritmaların yanıltıcı sıfır maliyet varsayması önlendi.
4. **Point-in-Time (PIT) & Lookahead Bias Koruması:** Geçmiş barların tamamlanma zamanı (`close_time`), verinin piyasaya çıkışı (`published_at`), sisteme gelişi (`received_at`), karara açılışı (`available_at`) ve veritabanına yazılışı (`ingested_at`) matematiksel olarak birbirinden ayrıldı.
5. **Kırılgan UTC Denetimi Sağlamlaştırıldı:** `ZoneInfo` veya farklı UTC gösterimlerinin `tzinfo != UTC` nedeniyle hata vermesi engellendi; UTC offset kontrolü ile normalize edildi.
6. **Fiyat Pozitifliği Dengelendi:** Forex ve altın için sıfır/negatif fiyatlar engellendi; negatif fiyat görebilen emtialar için `allow_negative_prices` parametresiyle evrensel kural dogmatizmi önlendi.

---

### 5. Önemli Tasarım Kararlarının Teknik Gerekçeleri

1. **`price_type` Alanında Varsayılan Bulunmaması:**
   - *Gerekçe:* Bir veri akışı (örneğin OANDA veya MetaTrader) sadece MID veya sadece BID veriyor olabilir. Sistem varsayılan olarak `BID` atarsa, araştırmacı farkında olmadan MID verisini BID gibi test eder; bu da canlı piyasada spread kayması nedeniyle stratejinin batmasına yol açar.
2. **`is_synthetic` Bayrağının Eklenmesi:**
   - *Gerekçe:* Yalnızca MID fiyatı veren bir sağlayıcıdan sentetik BID ve ASK üretildiğinde bu verinin gerçek piyasa kotasyonu olmadığı bilinmelidir. Gerçek borsa kotasyonu ile sentetik tahmin hiçbir zaman aynı güvenilirlikte kabul edilemez.
3. **`is_available_at` İçinde `is_closed` Kontrolü:**
   - *Gerekçe:* Canlı akışta 15 dakikalık bir mum 7. dakikasındayken de sisteme gelebilir. Ancak geçmiş zamanlı analiz kapalı mumları bekler. Kapanmamış bir mum, gelecekteki karar zamanlarında asla kapalı bar gibi kullanılamaz.
4. **Geçmiş Toplu İndirmede `AvailabilityConfidence.UNKNOWN` Zorunluluğu:**
   - *Gerekçe:* 2021 yılına ait bir veri bugün topluca indirildiğinde, o verinin 2021'deki tam ağ gecikmesi bilinemez. Sisteme sahte `published_at` veya `received_at` uydurmak kantitatif araştırmada veri sahteciliğidir (data dredging / leakage).

---

### 6. Eklenen Testlerin İsimleri ve Sayısı

`tests/test_core_types.py` içine eklenen testler (4 test):
- `TestPriceType::test_all_price_types_exist`
- `TestPriceType::test_distinct_values`
- `TestAvailabilityConfidence::test_confidence_levels`
- `TestEnsureUtc::test_zoneinfo_utc_normalized` (ve normalize testleri)

`tests/test_candle_pit_forex.py` içine eklenen testler (25 test):
1. `TestPriceTypeSemantics::test_explicit_price_type_required`
2. `TestPriceTypeSemantics::test_invalid_price_type_type_rejected`
3. `TestPriceTypeSemantics::test_all_price_types_accepted`
4. `TestPriceTypeSemantics::test_simultaneous_bid_and_ask_representation`
5. `TestPriceTypeSemantics::test_range_midpoint_is_not_market_midpoint`
6. `TestPriceTypeSemantics::test_spread_semantics_and_missing_spread`
7. `TestPriceTypeSemantics::test_negative_spread_rejected`
8. `TestPriceTypeSemantics::test_synthetic_data_flagging`
9. `TestPointInTimeAndBitemporal::test_all_six_timestamps_supported`
10. `TestPointInTimeAndBitemporal::test_default_ingested_at_is_utc_aware`
11. `TestPointInTimeAndBitemporal::test_naive_timestamp_in_bitemporal_fields_rejected`
12. `TestPointInTimeAndBitemporal::test_unclosed_candle_cannot_be_used_for_closed_analysis`
13. `TestPointInTimeAndBitemporal::test_is_available_at_with_verified_available_time`
14. `TestPointInTimeAndBitemporal::test_is_available_at_historical_backfill_without_available_at`
15. `TestPointInTimeAndBitemporal::test_revision_tracking`
16. `TestPointInTimeAndBitemporal::test_negative_revision_rejected`
17. `TestCandleValidationAndNormalization::test_utc_normalization_zoneinfo`
18. `TestCandleValidationAndNormalization::test_utc_normalization_zero_offset_timezone`
19. `TestCandleValidationAndNormalization::test_non_zero_utc_offset_rejected`
20. `TestCandleValidationAndNormalization::test_zero_or_negative_price_rejected_for_standard_instrument`
21. `TestCandleValidationAndNormalization::test_negative_prices_allowed_when_explicitly_configured`
22. `TestCandleValidationAndNormalization::test_equal_open_and_close_time_rejected`
23. `TestCandle::test_mid_price` (mevcut `test_domain_models.py` içinde `range_midpoint` ile genişletildi)

**Toplam Yeni Eklenen Test Sayısı:** **29 test**

---

### 7. Önceki ve Sonraki Test Sayısı

| Aşama | Toplam Test Sayısı | Başarılı | Başarısız | Süre |
| :--- | :---: | :---: | :---: | :---: |
| **Faz 0 Başlangıç** | 94 | 94 | 0 | 0.26s |
| **Faz 0.5A Sonuç** | **123** | **123** | **0** | **0.29s** |

---

### 8. Pytest, Ruff ve Mypy Doğrulama Sonuçları

#### Pytest
```text
python -m pytest -v --tb=short
...
============================= 123 passed in 0.29s =============================
```

#### Ruff Linter
```text
ruff check src/ tests/
All checks passed!
```

#### Ruff Formatter
```text
ruff format --check src/ tests/
47 files already formatted
```

#### Mypy Strict Mode
```text
mypy src/traderlab/
Success: no issues found in 37 source files
```

---

### 9. Başarısız Testler ve Nedenleri

- **Başarısız test sayısı:** **0**
- Tüm eski testler (94 adet) ve yeni eklenen testler (29 adet) sıfır regresyon ile doğrudan geçmiş, mimari AST testleri (`test_domain_has_no_forbidden_imports`) tam yeşil kalmıştır.

---

### 10. Kalan Teknik Riskler

1. **Broker-Spesifik Pip ve Lot Boyutları:** `Instrument` modeli halen statik `lot_size` ve `tick_size` tutmaktadır; farklı brokerların mikro lot (0.01 lot) ve 3-basamaklı XAUUSD fiyatlamaları `DataSource` seviyesinde override edilmelidir (Faz 0.5B kapsamı).
2. **Asenkron/Senkron Port Tercihi:** Veritabanı ve veri sağlayıcı portlarının async (`async def`) olması gerekliliği Faz 1 PostgreSQL aşamasında somutlaştırılacaktır.

---

### 11. Henüz Uygulanmayan Özellikler

- PostgreSQL tabloları ve Alembic migration scriptleri (Faz 1).
- Canlı WebSocket veya broker API veri sağlayıcı adaptörleri (Faz 1).
- ICT/SMC Piyasa Yapısı modelleri (Faz 2).
- Backtesting motoru ve strateji bileşenleri (Faz 3).

---

### 12. Faz 0.5B İçin Gereken Ön Koşullar

1. Kullanıcının Faz 0.5A raporunu ve domain değişikliklerini onaylaması.
2. `Instrument` ve `DataSource` modellerine broker-spesifik `pip_size`, `tick_size_override` ve `lot_size_override` yeteneklerinin eklenmesi.
3. `core/config.py` içindeki `DatabaseSettings` sınıfının altyapı katmanına taşınarak core katmanının pydantic-settings'ten arındırılması.

---

### Sonuç ve Onay Çağrısı

FAZ 0.5A hedefleri eksiksiz tamamlanmış, tüm matematiksel ve zamansal doğrulama kuralları testlerle mühürlenmiştir. 

Kesin sınır kuralları uyarınca işlem burada durdurulmuştur. **FAZ 0.5B'ye geçmeden önce onayınızı bekliyorum.**
