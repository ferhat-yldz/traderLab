# TRADERLAB — FAZ 0.5C NİHAİ MİMARİ DENETİM VE FAZ 1 HAZIRLIK RAPORU

**Tarih:** 2026-10-19  
**Denetçi Rolü:** Principal Software Architect, Senior Quantitative Trading Systems Engineer & Independent Quality Auditor  
**Kapsam:** Faz 0, Faz 0.5A, Faz 0.5B ve Faz 0.5C kod, mimari, veri güvenilirliği ve test denetimi  
**Nihai Karar:** **`READY_WITH_LIMITATIONS`**

---

## 1. FAZ 0 BAŞLANGIÇ DURUMU

TRADERLAB projesi, çoklu finansal piyasalarda (başta Forex/Altın, ileride Kripto, BIST, ABD Endeks Vadeli İşlemleri) bilimsel kantitatif araştırma, teknik analiz, piyasa yapısı (ICT/SMC), tarihsel benzerlik ve risk yönetimi amacıyla Python 3.11.9 üzerinde başlatılmıştır.

### Faz 0 Temel Hedefleri ve İlkeleri
- **Modular Monolith Mimarisi:** Dağıtık mikroservis karmaşasından kaçınarak süreç içi çağrılarla yüksek performans ve temiz modül sınırları sağlama ([ADR-001](file:///c:/trader/traderLab/traderlab/docs/decisions/adr-log.md#L1-L28)).
- **Hexagonal Architecture (Ports & Adapters):** Domain katmanının veritabanı, broker veya harici API'lerden tamamen izole edilmesi ([ADR-002](file:///c:/trader/traderLab/traderlab/docs/decisions/adr-log.md#L30-L58)).
- **Hassas Finansal Veri Tipleri:** Kayan noktalı (float) aritmetik hatalarını önlemek için `decimal.Decimal` ve katı `UTC` zaman dilimi zorunluluğu ([ADR-003](file:///c:/trader/traderLab/traderlab/docs/decisions/adr-log.md#L60-L83)).
- **Kesin İzolasyon Sınırları:** Eski `borsa/` dizinine ve `trader_muhendis` veritabanına kesinlikle dokunulmaması.

---

## 2. FAZ 0.5A VE FAZ 0.5B SONUÇLARI

### Faz 0.5A İncelemesi
- **Eklenen Değerler:**
  - `PriceType` (BID, ASK, MID, UNKNOWN) zorunluluğu getirilerek örtük spread ve bid varsayımları kaldırıldı ([ADR-006](file:///c:/trader/traderLab/traderlab/docs/decisions/adr-log.md#L134-L165)).
  - Bitemporal 6 zaman damgası (`open_time`, `close_time`, `published_at`, `received_at`, `available_at`, `ingested_at`) ve `AvailabilityConfidence` modeli kuruldu ([ADR-007](file:///c:/trader/traderLab/traderlab/docs/decisions/adr-log.md#L167-L203)).
  - Geometrik bar ortası (`range_midpoint`), gerçek piyasa spread ortasından (`mid_price`) kavramsal olarak ayrıldı.
- **Faz 0.5A'dan Kalan Kusur:**
  - `Candle.is_available_at` içerisinde `ingested_at > as_of` kuralı koşulsuz uygulandığı için, sonradan indirilen tarihsel verilerle (bulk historical backfill) geçmiş backtest yapılması imkânsız hale gelmişti.

### Faz 0.5B İncelemesi
- **Giderilen Kusurlar ve Eklenen Yetenekler:**
  - `decision_time` (`as_of`) ile `knowledge_snapshot_time` birbirinden ayrıldı. `RESEARCH_ESTIMATED` modunda sonradan indirilen verilerle backtest mümkün kılındı.
  - Veri tabanı konfigürasyonu (`DatabaseSettings`) `core/config.py` içerisinden altyapı katmanına (`infrastructure/database/config.py`) taşındı ([ADR-010](file:///c:/trader/traderLab/traderlab/docs/decisions/adr-log.md#L272-L295)).
  - Domain portları asenkron (`async def`) protokoller haline getirildi ([ADR-008](file:///c:/trader/traderLab/traderlab/docs/decisions/adr-log.md#L205-L235)).
  - Forex enstrüman modeline `pip_size` eklendi; broker-spesifik mikro-yapı sapmaları `DataSource` modelinde override alanlarıyla karşılandı ([ADR-009](file:///c:/trader/traderLab/traderlab/docs/decisions/adr-log.md#L237-L270)).

---

## 3. MİMARİ SINIRLARIN DURUMU

AST tabanlı mimari sınır testleri ([`tests/test_architecture.py`](file:///c:/trader/traderLab/traderlab/tests/test_architecture.py)) ile sistem genelinde katman bağımlılıkları denetlenmiştir:

1. **Domain Bağımsızlığı:**
   - Domain katmanı yalnızca standart kütüphaneyi ve `traderlab.core.types` ile `traderlab.core.errors` modüllerini import etmektedir.
   - `traderlab.core.config` modülü domain katmanı için yasaklanmıştır (hiçbir domain modülü ortam değişkenlerine veya ayarlara bağımlı değildir).
   - Domain katmanında SQLAlchemy, psycopg, Alembic, httpx, requests, FastAPI, ccxt, OpenAI veya pydantic-settings kesinlikle bulunmamaktadır.
2. **Application → Infrastructure İlişkisi:**
   - Application katmanı doğrudan somut veritabanı veya broker sınıflarını import etmemekte, yalnızca `traderlab.domain.ports` protokollerine bağımlı çalışmaktadır (DIP tam uyumu).
3. **Configuration Ayrımı:**
   - Uygulama ayarları ([`traderlab.core.config`](file:///c:/trader/traderLab/traderlab/src/traderlab/core/config.py)) sadece üst seviye ortam ve log ayarlarını yönetir.
   - Veritabanı ayarları ([`traderlab.infrastructure.database.config`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/config.py)) altyapı katmanı sınırları içine hapsedilmiştir.
4. **`@runtime_checkable Protocol` Analizi:**
   - `typing.runtime_checkable` Python'da yalnızca metot ve özellik isimlerinin varlığını denetler; parametre tiplerini veya `async` coroutine dönüş değerini runtime'da denetlemez.
   - Bu yüzeysel kontrolün yetersizliği, [`tests/test_domain_ports.py`](file:///c:/trader/traderLab/traderlab/tests/test_domain_ports.py) dosyasında fake adapter'lar üzerinden gerçek `await` operasyonları, coroutine çalıştırma ve tip doğrulaması yapılarak güvenceye alınmıştır.

---

## 4. SOLID İLKELERİNİN GERÇEK KOD ÜZERİNDEN DEĞERLENDİRİLMESİ

| İlke | Somut Uygulama Örneği | İnceleme ve Değerlendirme |
| :--- | :--- | :--- |
| **Single Responsibility (SRP)** | [`Instrument`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/instruments/instrument.py) vs [`DataSource`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/instruments/data_source.py) vs [`DatabaseSettings`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/config.py) | Enstrüman tanımı (piyasa standardı), veri akışı (broker beslemesi) ve veritabanı bağlantısı net sınırlarla ayrılmıştır. |
| **Open/Closed (OCP)** | [`DataSource.effective_*`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/instruments/data_source.py#L62-L87) | Yeni bir broker mikro-yapısı veya farklı spread modeli eklemek için kanonik `Instrument` sınıfını değiştirmeye gerek yoktur; `DataSource` üzerinden override edilir. |
| **Liskov Substitution (LSP)** | [`FakeCandleRepository`](file:///c:/trader/traderLab/traderlab/tests/test_domain_ports.py#L70-L113) & [`CandleRepository`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/ports.py#L48-L77) | Sahte repository ve provider adapter'ları, protokolün tüm asenkron sözleşmesini eksiksiz yerine getirmekte ve sistem davranışını bozmadan birbiri yerine geçebilmektedir. |
| **Interface Segregation (ISP)** | [`domain/ports.py`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/ports.py) | Monolitik bir `Repository` yerine `InstrumentRepository`, `DataSourceRepository`, `CandleRepository` ve `MarketDataProvider` olarak ayrıştırılmıştır. |
| **Dependency Inversion (DIP)** | [`MarketDataProvider`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/ports.py#L80-L98) | Use case'ler ve analiz motoru doğrudan broker API sınıflarına değil, domain katmanında tanımlı abstract protokollere bağımlıdır. |

---

## 5. POINT-IN-TIME (PIT) GÜVENİLİRLİĞİ VE SINIRLARI

### A. Senaryo Analizleri
1. **2024 Fiyat Verisinin 2026'da İndirilmesi:**
   - `t_open = 2024-01-15 10:00`, `t_close = 2024-01-15 11:00`, `ingested_at = 2026-10-19 12:00`.
   - `decision_time = 2024-01-15 11:05`:
   - `STRICT_PIT`: `ingested_at > decision_time` ve `available_at is None` olduğu için reddeder (`False`).
   - `RESEARCH_ESTIMATED`: `close_time (11:00) <= decision_time (11:05)` olduğu için kabul eder (`True`). Tarihsel araştırma engellenmez.
2. **2024 Verisinin 2026'da Düzeltilmesi ve Eski Snapshot Sorgusu:**
   - `rev0` (2026-01-01'de kaydedildi), `rev1` (2026-02-01'de düzeltildi).
   - Snapshot sorgusu `knowledge_snapshot_time = 2026-01-15` olduğunda:
     - `rev0` kabul edilir (`True`).
     - `rev1` reddedilir (`False`), çünkü `ingested_at > snapshot_time`. Hindsight sızıntısı önlenir.
3. **Aynı Mumun Birden Fazla Revizyonunun Bulunması:**
   - [`FakeCandleRepository.get_candles`](file:///c:/trader/traderLab/traderlab/tests/test_domain_ports.py#L108-L113) içinde snapshot zamanına kadar geçerli olan revizyonlar arasından en yüksek `revision` numaralı tekil mum seçilerek deduplication yapılır; mükerrer bar dönüşü engellenir.
4. **STRICT_PIT Modunda Doğrulanmamış Erişilebilirlik:**
   - `availability_confidence in (ESTIMATED, UNKNOWN)` veya `available_at is None` olan barlar `STRICT_PIT` modunda doğrudan reddedilir.
5. **Kapanmamış Üst Zaman Dilimi Mumunun Alt Zaman Dilimi Kararına Sızması:**
   - H1 mumu (10:00 - 11:00) oluşurken, saat 10:15'teki bir M5 kararında:
     - Canlı akışta `is_closed=False` olduğu için reddedilir.
     - Tarihsel veride `is_closed=True` olsa dahi `close_time (11:00) > decision_time (10:15)` olduğundan reddedilir.
     - Bu güvence [`test_unclosed_higher_timeframe_candle_rejected_at_lower_timeframe_decision`](file:///c:/trader/traderLab/traderlab/tests/test_candle_pit_forex.py#L695-L754) ile test edilmiştir.

### B. Açıkça Belgelenen Sınırlar
- **Domain Modeli Tek Başına SQL Bitemporal Garantisi Veremez:** `Candle.is_available_at` fonksiyonu bir değer nesnesi filtresidir. Milyonlarca satırlık veritabanında bu mantığın SQL `WHERE` ve indeksleme seviyesinde (`ingested_at <= snapshot_time AND available_at <= decision_time`) çalıştırılması Faz 1'in sorumluluğundadır.
- **Ağ Gecikmesi Modeli:** Tarihsel verilerde `available_at` olmadığında `close_time` baz alınır. Canlı işlemlerdeki 50ms–2000ms broker yayın gecikmesi araştırmada varsayımsal kalır.

---

## 6. FOREX PİYASA MİKRO-YAPISININ DOĞRULUĞU

1. **Fiyat Türleri (BID, ASK, MID, UNKNOWN):**
   - Her `Candle` nesnesi açık bir `PriceType` taşır.
   - `UNKNOWN` fiyat serisi depolanabilir ancak [`validate_for_trading()`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/instruments/candle.py#L172-L181) ile işlem fiyatlamasından kesinlikle men edilir.
   - Alış emirleri ASK üzerinden, satış emirleri BID üzerinden yürütülmelidir; tekil bar üzerinde BID/ASK olmadan spread varsayımı yapılamaz.
2. **`pip_value_ratio` vs Parasal Pip Değeri (`monetary_pip_value`):**
   - Yapılan denetimde `pip_value_ratio` özelliğinin parasal pip değeriyle karıştırılma riski tespit edilmiş ve düzeltilmiştir:
     - [`pip_value_ratio`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/instruments/instrument.py#L73-L81): `pip_size / tick_size` (boyutsuz oran; ör. EURUSD 0.0001 / 0.00001 = 10 pipette = 1 pip).
     - [`monetary_pip_value`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/instruments/instrument.py#L83-L91): `lot_size * pip_size` (karşıt para birimi cinsinden 1 lot için pip nakit değeri; ör. EURUSD 100,000 * 0.0001 = $10.00 USD).
3. **`MarketDataProvider.fetch_candles` Fiyat Türü Parametresi:**
   - Faz 0.5C denetiminde `MarketDataProvider.fetch_candles` portuna `*, price_type: PriceType = PriceType.BID` parametresi eklenerek sağlayıcıdan hangi serinin talep edildiği açık hale getirilmiştir.
4. **Veri Akışı vs Emir Gerçekleştirme:**
   - `DataSource` veri sağlayıcısının sunduğu fiyat akışı mikro-yapısını (`tick_size_override`, `typical_spread` vb.) taşır. Minimum emir boyutu, emir adımı, komisyon, marjin ve swap gibi emir gerçekleştirme koşulları Faz 4'teki broker execution modeline bırakılmıştır.

---

## 7. TEST ENVANTERİ TUTARSIZLIĞININ İNCELENMESİ VE ÇÖZÜMÜ

### Tespit Edilen Tutarsızlık
Faz 0.5B raporunda 144 testin başarılı olduğu belirtilmiş, ancak tablodaki sütun toplamı **145** görünmüştür.

### Kök Neden Analizi
Pytest koleksiyonu dosya bazında çalıştırılarak denetlenmiştir:
- Rapor tablosunda `test_config.py` test sayısı yanlışlıkla **11** olarak yazılmıştır.
- Gerçekte Faz 0.5A'da `test_config.py` dosyasında **8** test bulunmaktaydı (`TestDatabaseSettings` 3 test, `TestBuildDsn` 1 test, `TestAppSettings` 2 test, `TestGetSettings` 2 test).
- Faz 0.5B'de `TestGetDbSettings` ile **2** test eklenmiş ve dosyadaki test sayısı **8 + 2 = 10** olmuştur.
- Rapordaki tablonun önceki satırından kalan yanlış başlangıç sayısı (9) nedeniyle 9 + 2 = 11 yazılmış ve bu durum 1 testlik yapay bir tablo toplamı hatasına (145) yol açmıştır.

### Gerçek Test Envanteri Doğrulaması (Faz 0.5C Öncesi ve Sonrası)
- Faz 0.5B Gerçek Pytest Test Sayısı: **144**
- Faz 0.5C ile Eklenen Testler: **+3 test**
  1. `tests/test_candle_pit_forex.py`: `test_unclosed_higher_timeframe_candle_rejected_at_lower_timeframe_decision`
  2. `tests/test_domain_ports.py`: `test_multiple_revisions_returns_highest_valid_revision`
  3. `tests/test_domain_ports.py`: `test_fetch_candles_price_type_filtering`
- **Faz 0.5C Güncel Toplam Test Sayısı:** **147 test** (Tüm dosya toplamları tam olarak 147 vermektedir).

---

## 8. TEST SAYISI VE KATEGORİ DAĞILIMI

| Test Dosyası | Test Kategorisi | Test Adedi | Durum |
| :--- | :--- | :---: | :---: |
| [`tests/test_architecture.py`](file:///c:/trader/traderLab/traderlab/tests/test_architecture.py) | Mimari Sınır / AST Boundary Tests | 2 | GEÇTİ |
| [`tests/test_candle_pit_forex.py`](file:///c:/trader/traderLab/traderlab/tests/test_candle_pit_forex.py) | Domain / Point-in-Time & Bitemporal | 28 | GEÇTİ |
| [`tests/test_config.py`](file:///c:/trader/traderLab/traderlab/tests/test_config.py) | Core & Infrastructure Konfigürasyon | 10 | GEÇTİ |
| [`tests/test_core_errors.py`](file:///c:/trader/traderLab/traderlab/tests/test_core_errors.py) | Hata Hiyerarşisi | 11 | GEÇTİ |
| [`tests/test_core_types.py`](file:///c:/trader/traderLab/traderlab/tests/test_core_types.py) | Temel Tipler, Enums, UTC Doğrulama | 17 | GEÇTİ |
| [`tests/test_domain_models.py`](file:///c:/trader/traderLab/traderlab/tests/test_domain_models.py) | Domain Değer Nesneleri & Microstructure | 27 | GEÇTİ |
| [`tests/test_domain_ports.py`](file:///c:/trader/traderLab/traderlab/tests/test_domain_ports.py) | Asenkron Port Sözleşmeleri & Fake Adapters | 10 | GEÇTİ |
| [`tests/test_forex_instruments.py`](file:///c:/trader/traderLab/traderlab/tests/test_forex_instruments.py) | Forex Kanonik Tanımlar (XAUUSD, EURUSD) | 3 | GEÇTİ |
| [`tests/test_smoke.py`](file:///c:/trader/traderLab/traderlab/tests/test_smoke.py) | Import Smoke Tests & Sürüm Kontrolü | 39 | GEÇTİ |
| **GENEL TOPLAM** | **Bütün Kategoriler** | **147** | **Eksiksiz Başarılı** |

---

## 9. OTOMATİK KALİTE KONTROL ÇIKTILARI

### 1. Pytest Test Koşumu
```text
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-8.4.2, pluggy-1.7.0 -- C:\trader\traderLab\traderlab\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\trader\traderLab\traderlab
configfile: pyproject.toml
plugins: asyncio-0.26.0
asyncio: mode=Mode.STRICT, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 147 items

tests/test_architecture.py ..                                            [  1%]
tests/test_candle_pit_forex.py ............................              [ 20%]
tests/test_config.py ..........                                          [ 27%]
tests/test_core_errors.py ...........                                    [ 35%]
tests/test_core_types.py .................                               [ 46%]
tests/test_domain_models.py ...........................                  [ 65%]
tests/test_domain_ports.py ..........                                    [ 71%]
tests/test_forex_instruments.py ...                                      [ 73%]
tests/test_smoke.py .......................................              [100%]

============================= 147 passed in 0.36s =============================
```

### 2. Pytest Collect-Only Doğrulaması
```text
147 tests collected in 0.16s
```

### 3. Ruff Linter
```text
All checks passed!
```

### 4. Ruff Formatter
```text
49 files already formatted
```

### 5. Mypy Statik Tip Denetimi
```text
Success: no issues found in 38 source files
```

### 6. Mimari AST Sınır Testi
```text
tests/test_architecture.py::test_domain_has_no_forbidden_imports PASSED  [ 50%]
tests/test_architecture.py::test_domain_only_imports_core_and_stdlib PASSED [100%]
2 passed in 0.02s
```

---

## 10. FAZ 0.5C'DE DEĞİŞTİRİLEN DOSYALAR

1. [`src/traderlab/domain/instruments/instrument.py`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/instruments/instrument.py) — `pip_value_ratio` docstring açıklaması netleştirildi; parasal pip değerini dönen `monetary_pip_value` özelliği eklendi.
2. [`src/traderlab/domain/ports.py`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/ports.py) — `MarketDataProvider.fetch_candles` imzasına `*, price_type: PriceType = PriceType.BID` eklendi.
3. [`tests/test_domain_ports.py`](file:///c:/trader/traderLab/traderlab/tests/test_domain_ports.py) — `FakeCandleRepository.get_candles` metoduna çoklu revizyon tekilleştirme (deduplication) eklendi; `FakeMarketDataProvider` fiyat türü filtresine bağlandı; 2 yeni sözleşme testi eklendi.
4. [`tests/test_candle_pit_forex.py`](file:///c:/trader/traderLab/traderlab/tests/test_candle_pit_forex.py) — Üst zaman dilimi mumunun alt zaman dilimi kararına sızmasını engelleyen regresyon testi eklendi.
5. [`tests/test_domain_models.py`](file:///c:/trader/traderLab/traderlab/tests/test_domain_models.py) — `monetary_pip_value` testi eklendi.
6. [`tests/test_forex_instruments.py`](file:///c:/trader/traderLab/traderlab/tests/test_forex_instruments.py) — `xauusd` ve `eurusd` için `monetary_pip_value` testleri eklendi.
7. [`docs/reports/phase-0.5-final.md`](file:///c:/trader/traderLab/traderlab/docs/reports/phase-0.5-final.md) — Bu kapsamlı denetim raporu oluşturuldu.

---

## 11. EKLENEN REGRESYON TESTLERİ

1. **`test_unclosed_higher_timeframe_candle_rejected_at_lower_timeframe_decision`** ([`tests/test_candle_pit_forex.py:L695`](file:///c:/trader/traderLab/traderlab/tests/test_candle_pit_forex.py#L695)):  
   10:00–11:00 H1 mumunun, henüz saat 10:15 iken simüle edilen bir M5 kararında hem `is_closed=False` iken hem de sonradan indirilmiş veri setinde `close_time > decision_time` nedeniyle reddedildiğini kanıtlar.
2. **`test_multiple_revisions_returns_highest_valid_revision`** ([`tests/test_domain_ports.py:L257`](file:///c:/trader/traderLab/traderlab/tests/test_domain_ports.py#L257)):  
   Aynı muma ait birden fazla revizyon bulunduğunda (`rev0` ve `rev1`), snapshot tarihine göre tek bir doğru versiyonun seçildiğini ve mükerrer kayıt üretilmediğini kanıtlar.
3. **`test_fetch_candles_price_type_filtering`** ([`tests/test_domain_ports.py:L303`](file:///c:/trader/traderLab/traderlab/tests/test_domain_ports.py#L303)):  
   Veri sağlayıcısından BID ve ASK mumlarının ayrı ayrı ve eksiksiz filtrelenerek çekilebildiğini kanıtlar.

---

## 12. GİDERİLEN VE AÇIK KALAN SORUNLAR

### Giderilen Sorunlar
- [x] Tarihsel backfill indirmelerinde `ingested_at > as_of` blokajı kaldırıldı; `decision_time` ve `knowledge_snapshot_time` ayrıldı.
- [x] Veritabanı konfigürasyonu core katmanından arındırılarak altyapı katmanına taşındı.
- [x] Senkron portlar asenkron (`async def`) protokollere dönüştürüldü.
- [x] İlkel string `symbol` takıntısı kaldırıldı, `InstrumentId` kullanıldı.
- [x] Forex enstrüman modeline `pip_size` ve parasal pip değeri eklendi.
- [x] `DataSource` seviyesinde broker mikro-yapı override desteği sağlandı.
- [x] Rapor tablosundaki 144 vs 145 test sayısı tutarsızlığı çözüldü ve doğrulandı.
- [x] ADR kayıtlarındaki hatalı 2024 tarihleri 2026 olarak düzeltildi.

### Açık Kalan (Bilinçli Olarak Faz 1 ve Sonrasına Bırakılan) Konular
- Veritabanı henüz oluşturulmadı (SQL seviyesinde bitemporal indeksleme ve partition optimizasyonu Faz 1 konusudur).
- Gerçek broker API adaptörleri (MetaTrader 5 / OANDA REST / CCXT) henüz kodlanmadı.
- Canlı ağ yayın gecikmesi (network publication latency) canlı veri beslemesi bağlanana kadar ampirik olarak test edilemez.

---

## 13. HENÜZ TEST EDİLMEMİŞ GERÇEK ALTYAPI BİLEŞENLERİ

Sözleşme sınırlarına sadık kalındığı için aşağıdaki bileşenler henüz test edilmemiştir ve gerçek ortamda bulunmamaktadır:
1. **Canlı PostgreSQL Veritabanı & asyncpg/SQLAlchemy Pool:** Bellek içi sözleşmeler doğrulanmış, ancak gerçek veritabanı bağlantısı kurulmamıştır.
2. **Alembic Veritabanı Migrationları:** Tablolar oluşturulmamıştır.
3. **Gerçek Broker / Vendor Ağ Bağlantısı:** Canlı ağ kesintileri, API rate-limit sınırları ve reconnect mekanizmaları henüz çalıştırılmamıştır.

---

## 14. FAZ 1 VERİ ALTYAPISI İÇİN HAZIRLIK TASARIMI

Faz 1'e geçildiğinde uygulanacak veri mimarisi tasarlanmıştır:

### A. PostgreSQL Tablo ve Sürümleme Mimarisi
İki tablo modeli önerilmektedir:
1. `candles`: Geçerli/aktif mum verileri (hızlı okuma ve strateji besleme).
2. `candle_revisions`: Geçmiş düzeltmelerin ve denetim izlerinin (audit trail) tutulduğu bitemporal revizyon tablosu.

```sql
-- Faz 1 Taslak Şeması
CREATE TABLE candles (
    instrument_id VARCHAR(32) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    timeframe INT NOT NULL,
    open_time TIMESTAMPTZ NOT NULL,
    close_time TIMESTAMPTZ NOT NULL,
    price_type VARCHAR(8) NOT NULL,
    open NUMERIC(18, 8) NOT NULL,
    high NUMERIC(18, 8) NOT NULL,
    low NUMERIC(18, 8) NOT NULL,
    close NUMERIC(18, 8) NOT NULL,
    volume NUMERIC(18, 4) NOT NULL,
    spread NUMERIC(18, 8),
    is_closed BOOLEAN NOT NULL DEFAULT TRUE,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ,
    available_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    availability_confidence VARCHAR(16) NOT NULL,
    revision INT NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_id, source_id, timeframe, open_time, price_type, revision)
);

-- Point-in-Time ve Snapshot Hızlı Arama İndeksi
CREATE INDEX idx_candles_pit_lookup ON candles (
    source_id, timeframe, price_type, open_time, ingested_at, available_at
);
```

### B. Tekil Doğru Versiyon Seçim Sorgusu (Bitemporal Snapshot Query)
```sql
SELECT DISTINCT ON (open_time, price_type) *
FROM candles
WHERE source_id = :source_id
  AND timeframe = :timeframe
  AND open_time >= :start_utc AND open_time < :end_utc
  AND is_closed = TRUE
  AND (:price_type IS NULL OR price_type = :price_type)
  AND (ingested_at <= :knowledge_snapshot_time)
  AND (
    CASE 
      WHEN :policy = 'STRICT_PIT' THEN (availability_confidence = 'VERIFIED' AND available_at <= :as_of_utc)
      ELSE (COALESCE(available_at, close_time) <= :as_of_utc)
    END
  )
ORDER BY open_time, price_type, revision DESC;
```

### C. İdempotent Ingestion & Veri Kalite Kontrolleri
- **Duplicate Prevention:** Aynı `(instrument, source, timeframe, open_time, price_type, revision)` kaydı gelirse `ON CONFLICT DO NOTHING`.
- **Gap Detection:** Mumlar ardışık incelenerek eksik zaman adımları (ör. M5 için 300 saniyelik sıçrama kontrolü) tespit edilmeli.
- **Outlier / Spike Filter:** `high >= low`, `high >= open, close`, `low <= open, close` doğrulaması Python domain nesnesinde fail-fast olarak yapılmalı.

---

## 15. FAZ 1 KABUL KRİTERLERİ

Faz 1'e başlandığında başarı ölçütü olarak aranacak kriterler:
1. Docker Compose veya yerel PostgreSQL 16 üzerinde test veritabanı ayağa kaldırılmalıdır.
2. Alembic migration scriptleri oluşturulmalı ve `candles` tablosu yukarıdaki PIT sütunlarıyla eksiksiz açılmalıdır.
3. `PostgresCandleRepository` adapter'ı `src/traderlab/infrastructure/database/` altına yazılmalı ve `CandleRepository` asenkron protokolünü tam olarak sağlamalıdır.
4. XAUUSD için en az bir gerçekçi veri sağlayıcısı adaptörü (`MarketDataProvider`) yazılmalıdır.
5. Ingestion use case'i (`fetch → validate → store`) idempotent çalışmalı, kopukluklarda kaldığı yerden devam edebilmelidir.
6. Gerçek veritabanı ile entegrasyon testleri (`tests/integration/`) çalıştırılmalıdır.

---

## 16. SON MÜHENDİSLİK DEĞERLENDİRMESİ

TRADERLAB platformu, Faz 0 ile Faz 0.5C arasındaki titiz çalışmalar sonucunda;
- Kayan nokta hatası barındırmayan (`Decimal`),
- Zaman dilimi karmaşasından uzak (`Strict UTC`),
- Alış/satış fiyatlarını gizlemeyen (`Explicit BID/ASK/MID`),
- Bitemporal olay ve bilgi zamanlarını ayıran,
- Domain bağımsızlığını AST testleriyle koruyan,
- I/O portları asenkron tasarlanmış,
- 147 otomatik test ile korunan

sağlam, temiz ve genişletilebilir bir mimari temele kavuşmuştur.

### Mimari Kabul Kararı:

# **`READY_WITH_LIMITATIONS`**

**Gerekçe:**  
Kod tabanı, domain modelleri, port sözleşmeleri ve mimari sınırlar Faz 1 geliştirmesine başlamak için bütünüyle **HAZIRDIR**. "WITH_LIMITATIONS" şerhi, PostgreSQL veritabanı ve gerçek broker ağ entegrasyonunun henüz kodlanmamış olmasından kaynaklanmaktadır. Bu sınırlamalar Faz 1'in doğal kapsamıdır.

---

## TALİMAT GEREĞİ DURDURULDU

Faz 1'e otomatik geçiş yapılmamıştır. Kullanıcının onayı beklenmektedir.
