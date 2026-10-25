# TRADERLAB — FAZ 0.5A Son Kabul ve Mühendislik Raporu
## Point-in-Time Politikaları, Ingestion Semantiği ve Fiyat Doğrulama

**Tarih:** 2024-10-19  
**Aşama:** FAZ 0.5A Final  
**Rol:** Senior Python Software Architect, Quantitative Trading Systems Engineer & Financial Data Engineer  
**Durum:** Tamamlandı (Tüm Kabul Kriterleri Karşılandı)

---

### 1. Genel Bakış ve Yapılan Düzeltmeler

Faz 0.5A son kabul denetimi kapsamında, mimarinin profesyonel kantitatif araştırma standartlarını tam olarak sağlaması amacıyla 3 kritik alanda kesinleştirme yapılmıştır:

1. **PIT Erişilebilirlik Politikası (`PitPolicy`):**
   - `available_at=None` olan geçmiş verilerin otomatik olarak `close_time` üzerinden "doğrulanmış PIT" gibi kullanılması engellendi.
   - `PitPolicy.STRICT_PIT` politikası varsayılan kılındı: Sadece `availability_confidence == VERIFIED` ve `available_at <= as_of` olan veriler kabul edilir. `UNKNOWN` veya `ESTIMATED` veriler reddedilir.
   - `PitPolicy.RESEARCH_ESTIMATED` politikası eklendi: Tarihsel geriye dönük araştırmalarda (toplu backfill), tamamlanmış mumlar için `close_time <= as_of` varsayımı yalnızca bu politika açıkça talep edildiğinde geçerli olur.
   - Karar anından sonraki veri revizyonlarının ve düzeltmelerin geçmişe sızmasını (leakage) önlemek için `ingested_at > as_of` denetimi eklendi.
2. **`ingested_at` Semantiği ve Katman Ayrımı:**
   - Bellekte yeni oluşturulan `Candle` nesnelerinin `ingested_at` alanına otomatik `utc_now()` atanması iptal edildi; varsayılan değer `None` yapıldı.
   - Domain nesnesi oluşturma anı ile veritabanına yazılma (persistence commit) anı ayrıştırıldı. Bu alanın Faz 1'deki `CandleRepository` implementasyonunda veritabanı kayıt zamanı olarak damgalanacağı sözleşmeyle güvenceye alındı.
3. **`PriceType.UNKNOWN` ve Ticaret Doğrulaması:**
   - Ham veri akışlarında fiyat tipi tespit edilemeyen barların `PriceType.UNKNOWN` olarak saklanabilmesi sağlandı.
   - Ancak strateji, emir gerçekleştirme veya fiyatlama hesaplamalarında `PriceType.UNKNOWN` kullanımını kesin olarak engelleyen `validate_for_trading()` kuralı ve `is_tradable_price` niteliği eklendi.
   - Bilinmeyen spread'in (`spread=None`) sıfır sayılmaması kuralı korundu.

---

### 2. Değiştirilen Dosyalar ve Eski/Yeni Davranış Karşılaştırması

| Dosya Yolu | Eski Davranış | Yeni Davranış |
| :--- | :--- | :--- |
| `src/traderlab/core/types.py` | `PitPolicy` bulunmuyordu. | `PitPolicy.STRICT_PIT` ve `PitPolicy.RESEARCH_ESTIMATED` enumları eklendi ve dışa aktarıldı. |
| `src/traderlab/domain/instruments/candle.py` | `ingested_at = field(default_factory=utc_now)` bellekteki nesneyi otomatik kalıcı kaydedilmiş gibi gösteriyordu. | `ingested_at: datetime \| None = None` yapıldı. Yalnızca veritabanı commit anında doldurulacak altyapı sözleşmesine bağlandı. |
| `src/traderlab/domain/instruments/candle.py` | `is_available_at()` `available_at=None` olduğunda her zaman `close_time`'a güveniyordu (sahte PIT riski). | Varsayılan `PitPolicy.STRICT_PIT` ile unverified veriler reddedildi; `ingested_at > as_of` revizyon sızıntısı engellendi; `RESEARCH_ESTIMATED` seçimi zorunlu kılındı. |
| `src/traderlab/domain/instruments/candle.py` | `PriceType.UNKNOWN` için ticaret kontrolü yoktu. | `is_tradable_price` ve `validate_for_trading()` metodları eklendi. |
| `tests/test_candle_pit_forex.py` | 25 test bulunuyordu; `ingested_at` ve `PitPolicy` testleri eski sözleşmeye göreydi. | 28 teste çıkarıldı; `PitPolicy.STRICT_PIT`, `RESEARCH_ESTIMATED`, revizyon sızıntısı engelleme ve `validate_for_trading()` eklendi. |
| `docs/decisions/adr-log.md` | ADR-007 bitemporal yapıyı özetliyordu. | ADR-007, `PitPolicy` (Strict vs Research) ve `ingested_at` katman ayrımı ile güncellendi. |

---

### 3. Eklenen Yeni Testler

`tests/test_candle_pit_forex.py` dosyasına eklenen/güçlendirilen testler:

1. `test_default_ingested_at_is_none_for_in_memory_domain_objects`: Bellekte oluşturulan domain nesnelerinin `ingested_at=None` olduğunu, kalıcılık damgalaması yapılana kadar veritabanı kayıt zamanı taşımadığını doğrular.
2. `test_price_type_unknown_can_be_stored_but_fails_trading_validation`: `UNKNOWN` serilerin saklanabildiğini fakat ticaret/fiyatlama için çağrıldığında `InvalidCandleError` fırlattığını, `BID` serinin ise başarıyla doğrulandığını test eder.
3. `test_is_available_at_historical_backfill_without_available_at`: Unverified tarihsel verinin varsayılan `STRICT_PIT` altında reddedildiğini; yalnızca araştırmacı `PitPolicy.RESEARCH_ESTIMATED` politikasını açıkça seçtiğinde `close_time` sonrasında izin verildiğini doğrular.
4. `test_unverified_confidence_rejected_under_strict_pit`: `ESTIMATED` veya `UNKNOWN` güven derecesindeki verilerin `STRICT_PIT` altında `available_at` olsa dahi sahte güvenilirlikle kullanılmasını engeller.
5. `test_revision_ingested_after_as_of_rejected_to_prevent_leakage`: Saat 15:00'te sisteme kaydedilmiş bir veri düzeltmesinin (revision 1), saat 12:00'deki karar anında geçmişe sızamayacağını (`is_available_at == False`) garanti eder.

---

### 4. Test ve Doğrulama Kanıtları

Tüm test araçları ve statik analizler izole sanal ortamda (`.venv`, Python 3.11.9) çalıştırılmıştır:

#### A. Pytest Toplu Koşum (`python -m pytest -v --tb=short`)
```text
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-8.4.2, pluggy-1.7.0
rootdir: C:\trader\traderLab\traderlab
configfile: pyproject.toml
testpaths: tests
plugins: asyncio-0.26.0
collected 126 items

tests/test_architecture.py (2 test) PASSED                               [  1%]
tests/test_candle_pit_forex.py (28 test) PASSED                          [ 23%]
tests/test_config.py (8 test) PASSED                                     [ 30%]
tests/test_core_errors.py (11 test) PASSED                               [ 38%]
tests/test_core_types.py (16 test) PASSED                                [ 51%]
tests/test_domain_models.py (23 test) PASSED                             [ 69%]
tests/test_forex_instruments.py (3 test) PASSED                          [ 72%]
tests/test_smoke.py (35 test) PASSED                                     [100%]

============================= 126 passed in 0.30s =============================
```

#### B. Pytest Koleksiyon Doğrulaması (`python -m pytest --collect-only -q`)
```text
126 tests collected in 0.21s
```

#### C. Ruff Linter (`ruff check src/ tests/`)
```text
All checks passed!
```

#### D. Ruff Formatter (`ruff format --check src/ tests/`)
```text
47 files already formatted
```

#### E. Mypy Strict Mode (`mypy src/traderlab/`)
```text
Success: no issues found in 37 source files
```

---

### 5. Başarısız Testler ve Nedenleri

- **Başarısız Test Sayısı:** **0**
- Tüm 126 test sıfır hata ve sıfır uyarıyla başarıyla tamamlanmıştır.

---

### 6. Henüz Uygulanmayan ve Yalnızca Domain Düzeyinde Doğrulanan Hususlar

1. **Veri Revizyonlarının Bitemporal Sorgulanması:** Karar anından sonraki revizyonların geçmişe sızmaması şu an domain nesnesi üzerinde `ingested_at > as_of` mantığı ile test edilmiş ve doğrulanmıştır. Ancak çoklu revizyonların veritabanı seviyesinde `(instrument_id, source_id, timeframe, open_time, revision)` bileşik anahtarıyla saklanması ve SQL düzeyinde `as_of_utc` bitemporal join ile filtrelenmesi **Faz 1 (PostgreSQL & Alembic)** aşamasında uygulanacaktır.
2. **Broker-Spesifik Lot ve Pip Boyutları:** `Instrument` ve `DataSource` seviyesinde broker bazlı dinamikler **Faz 0.5B** aşamasında ele alınacaktır.
3. **Core / Infrastructure Konfigürasyon Temizliği:** `DatabaseSettings` sınıfının `core/config.py` içerisinden `infrastructure/` altına taşınması **Faz 0.5B** aşamasında yapılacaktır.

---

### 7. Faz 0.5B'ye Geçiş Durumu

Faz 0.5A için istenen tüm son kabul düzeltmeleri eksiksiz olarak yerine getirilmiş; fiyat modelinin, point-in-time güvenilirliğinin ve katman sınırlarının mimari sağlamlığı kanıtlanmıştır.

Kesin sınırlar kuralı gereğince burada durulmuştur. **FAZ 0.5B geliştirmelerine başlamak için onayınızı bekliyorum.**
