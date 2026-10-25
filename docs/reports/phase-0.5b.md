# TRADERLAB — FAZ 0.5B KAPSAMLI TESLİM RAPORU

**Tarih:** 2026-10-19  
**Aşama:** Faz 0.5B — SOLID, Clean Architecture, Async Portlar, Forex Broker Metadata ve Bitemporal PIT İyileştirmeleri  
**Nihai Durum:** `READY`

---

## 1. BAŞLANGIÇ SİSTEM DURUMU

Faz 0.5B çalışmasına başlamadan önce sistemin mevcut durumu denetlenmiştir:
- **Python Sürümü:** Python 3.11.9 (64-bit Windows)
- **Çalışma Alanı:** `c:\trader\traderLab\traderlab`
- **İzolasyon Sınırları:** Eski `borsa/` dizinine veya `trader_muhendis` veritabanına kesinlikle dokunulmamıştır.
- **Veritabanı / Migration Durumu:** Hiçbir PostgreSQL tablosu oluşturulmamış, Alembic migration çalıştırılmamıştır.
- **Piyasa Verisi:** Hiçbir canlı piyasa verisi indirilmemiş, broker API bağlantısı kurulmamıştır.
- **Mevcut Rapor İncelemesi:** `docs/reports/phase-0.5a.md`, `docs/reports/phase-0.5a-final.md`, `docs/architecture.md` ve `docs/decisions/adr-log.md` dosyaları taranmıştır.

---

## 2. BAŞLANGIÇ TEST SONUÇLARI

Faz 0.5B öncesi test envanteri:
- **Faz 0.5A Kapanış Test Sayısı:** 126 test (126 passed in 0.31s).
- **Smoke Test Sayısı Doğrulaması:** `tests/test_smoke.py` içerisinde 37 paket parametrizasyonu + 1 sürüm testi = 38 smoke testi mevcuttur. Önceki raporda yer alan "35 smoke testi" ifadesinin yazım hatası (clerical typo) olduğu, hiçbir smoke testin silinmediği veya azaltılmadığı tespit edilmiştir.
- **Başlangıç Çalıştırma:**
  ```text
  126 passed in 0.31s
  ```

---

## 3. DEĞİŞTİRİLEN VE EKLENEN DOSYALARIN TAM LİSTESİ

### Eklenen Yeni Dosyalar
1. `src/traderlab/infrastructure/database/config.py` — PostgreSQL bağlantı parametreleri, SecretStr şifreleme ve DSN oluşturma mantığı.
2. `tests/test_domain_ports.py` — Asenkron port arayüzleri ve fake adapter sözleşme testleri (8 test).
3. `docs/reports/phase-0.5b.md` — Faz 0.5B resmi kabul ve teslim raporu.

### Değiştirilen Mevcut Dosyalar
1. `src/traderlab/domain/instruments/candle.py` — `is_available_at` fonksiyonunda `decision_time` (`as_of`) ve `knowledge_snapshot_time` ayrımı; bitemporal revizyon sızıntısı engelleme.
2. `src/traderlab/domain/instruments/instrument.py` — `pip_size: Decimal` alanı, `pip_size > 0` doğrulaması ve `pip_value_ratio` türetilmiş özelliği eklendi.
3. `src/traderlab/domain/instruments/data_source.py` — Broker mikro-yapı override alanları (`tick_size_override`, `pip_size_override`, `lot_size_override`, `price_precision_override`, `typical_spread`) ve `effective_*` çözümleme metotları eklendi.
4. `src/traderlab/domain/ports.py` — I/O portları strictly asenkron (`async def`) hâle getirildi; `MarketDataProvider` içinde ilkel string `symbol` yerine `InstrumentId` kullanıldı; `@runtime_checkable` eklendi.
5. `src/traderlab/core/config.py` — Altyapı bağımlılığı olan `DatabaseSettings` core katmanından tamamen kaldırıldı; sadece uygulama seviyesi konfigürasyon (`Environment`, `AppSettings`) bırakıldı.
6. `src/traderlab/infrastructure/database/__init__.py` — `DatabaseSettings` ve `build_dsn` fonksiyonu `infrastructure.database.config` altından re-export edildi.
7. `src/traderlab/markets/forex/instruments.py` — `xauusd()` (`pip_size=0.10`) ve `eurusd()` (`pip_size=0.0001`) tanımları güncellendi.
8. `tests/conftest.py` — `xauusd` test fixture'ına `pip_size=Decimal("0.10")` eklendi.
9. `tests/test_config.py` — `DatabaseSettings` importu altyapı katmanına yönlendirildi; `TestGetDbSettings` eklendi.
10. `tests/test_architecture.py` — Domain katmanı için yasaklı prefixlere `traderlab.core.config` ve `pydantic_settings` eklendi.
11. `tests/test_domain_models.py` — `pip_size`, `pip_value_ratio` ve `DataSource` broker override testleri eklendi.
12. `tests/test_forex_instruments.py` — `xauusd` ve `eurusd` pip boyutu ve pip-to-tick oranı kontrolleri eklendi.
13. `tests/test_candle_pit_forex.py` — Tarihsel backfill ve bitemporal snapshot revizyon filtreleme testleri eklendi.
14. `tests/test_smoke.py` — `traderlab.infrastructure.database.config` import smoke testine eklendi.
15. `docs/decisions/adr-log.md` — 2024 tarihleri 2026-10-19 olarak düzeltildi; ADR-007 güncellendi; ADR-008, ADR-009 ve ADR-010 eklendi.
16. `docs/architecture.md` — Asenkron portlar, konfigürasyon ayrımı ve mikro-yapı modelleri mimari dokümantasyonuna işlendi.

---

## 4. HER DOSYADA YAPILAN DEĞİŞİKLİKLERİN GEREKÇESİ

| Dosya | İşlem Türü | Mimari Gerekçe |
| :--- | :--- | :--- |
| `src/traderlab/domain/instruments/candle.py` | Değiştirildi | `decision_time` ile `knowledge_snapshot_time` zaman eksenlerini ayırarak sonradan indirilen verilerin `RESEARCH_ESTIMATED` modunda kullanılabilmesini sağlarken, eski snapshot'lara gelecekteki revizyonların sızmasını engellemek. |
| `src/traderlab/domain/instruments/instrument.py` | Değiştirildi | Finansal enstrüman modelinde tick ile pip kavramlarını netleştirmek; Forex ve Altın için pip büyüklüğünü kanonik modele dahil etmek. |
| `src/traderlab/domain/instruments/data_source.py` | Değiştirildi | Farklı brokerların (OANDA, MetaTrader vb.) aynı enstrüman için sunduğu farklı tick/pip/lot/hassasiyet ve spread değerlerini kanonik enstrüman modelini kirletmeden `DataSource` seviyesinde override edebilmek. |
| `src/traderlab/domain/ports.py` | Değiştirildi | Ağ ve veritabanı I/O operasyonlarını asenkron (`async def`) yaparak thread-blocking darboğazlarını engellemek; ilkel string takıntısını (`symbol: str`) `InstrumentId` ile çözmek. |
| `src/traderlab/core/config.py` | Değiştirildi | Clean Architecture gereği çekirdek (core) katmanının PostgreSQL/psycopg altyapı ayarlarına bağımlı olmasını önlemek. |
| `src/traderlab/infrastructure/database/config.py` | Eklendi | Veritabanı bağlantı ayarlarını ve gizli anahtarları (`SecretStr`) altyapı katmanı sınırları içinde izole etmek. |
| `src/traderlab/infrastructure/database/__init__.py` | Değiştirildi | Geriye dönük uyumluluk için altyapı konfigürasyonunu re-export etmek. |
| `src/traderlab/markets/forex/instruments.py` | Değiştirildi | Forex CFD XAUUSD (pip=0.10) ve EURUSD (pip=0.0001) tanımlarına kanonik pip boyutunu işlemek. |
| `tests/test_architecture.py` | Değiştirildi | Domain katmanının yanlışlıkla `traderlab.core.config` veya `pydantic_settings` import etmesini CI/test seviyesinde AST ile yasaklamak. |
| `tests/test_domain_ports.py` | Eklendi | Asenkron repository ve data provider protokollerinin DIP ve LSP prensiplerine uygunluğunu fake adapter'lar ile test etmek. |
| `docs/decisions/adr-log.md` | Değiştirildi | Hatalı 2024 tarihlerini projenin gerçek çalışma yılı 2026 olarak düzeltmek; yeni mimari kararları ADR-008, ADR-009 ve ADR-010 ile kaydetmek. |

---

## 5. FAZ 0.5A'DAN KALAN PIT SORUNUNUN ÇÖZÜMÜ

### Tespit Edilen Hata
Faz 0.5A'da uygulanan `ingested_at > as_of` kuralı, canlı sistem yeniden oynatımlarında doğru olsa da, araştırma amaçlı toplu tarihsel veri indirmelerinde (bulk backfill) büyük bir mantık hatasına yol açıyordu. Örneğin, 2024 yılına ait mum verileri 2026 yılında veritabanına kaydedildiğinde, `ingested_at` (2026) değeri simüle edilen karar anından (`as_of` = 2024) büyük olduğu için `RESEARCH_ESTIMATED` modunda bile tüm tarihsel veriler reddediliyordu.

### Çözüm Mimarisi: Bitemporal İki Zaman Ekseni
İki zaman kavramı birbirinden açıkça ayrılmıştır:
1. **Market Decision Time (`as_of`):** Simüle edilen al/sat stratejisinin karar anı (olay zamanı / valid time ekseni).
2. **Knowledge Snapshot Time (`knowledge_snapshot_time`):** Veritabanı bilgi snapshot'ının zamanı (işlem zamanı / system state ekseni).

```python
# Candle.is_available_at mantığı:
as_of_norm = ensure_utc(as_of)
if not self.is_closed:
    return False

effective_snapshot = (
    ensure_utc(knowledge_snapshot_time)
    if knowledge_snapshot_time is not None
    else (as_of_norm if policy == PitPolicy.STRICT_PIT else None)
)

if (
    effective_snapshot is not None
    and self.ingested_at is not None
    and self.ingested_at > effective_snapshot
):
    return False

if policy == PitPolicy.STRICT_PIT:
    if self.availability_confidence != AvailabilityConfidence.VERIFIED:
        return False
    if self.available_at is None:
        return False
    return self.available_at <= as_of_norm

if policy == PitPolicy.RESEARCH_ESTIMATED:
    if self.available_at is not None:
        return self.available_at <= as_of_norm
    return self.close_time <= as_of_norm
```

### Korunan Güvenceler
- **`STRICT_PIT` Modunda:** `knowledge_snapshot_time` varsayılan olarak `as_of` kabul edilir (canlı sistem değişmezi: henüz veritabanına yazılmamış veri o anda bilinemez). Yalnızca `VERIFIED` güven seviyesine sahip ve `available_at <= as_of` olan veriler geçerlidir.
- **`RESEARCH_ESTIMATED` Modunda:** `knowledge_snapshot_time` belirtilmemişse, sonradan indirilmiş tarihsel verilerle araştırmaya izin verilir (`close_time <= as_of`).
- **Revizyon Sızıntısı Koruması:** `knowledge_snapshot_time` parametresi verildiğinde (örneğin 2026-01-15 snapshot'ı), bu tarihten sonra kaydedilmiş düzeltmeler (`revision=1`, `ingested_at = 2026-02-01`) filtrelenir ve geçmiş backtest'e sızamaz.

---

## 6. ESKİ VE YENİ PORT İMZALARI

### 1. `InstrumentRepository`
- **Eski (Senkron):**
  ```python
  def get_by_id(self, instrument_id: InstrumentId) -> Instrument | None: ...
  def list_all(self) -> Sequence[Instrument]: ...
  def save(self, instrument: Instrument) -> None: ...
  ```
- **Yeni (Asenkron):**
  ```python
  async def get_by_id(self, instrument_id: InstrumentId) -> Instrument | None: ...
  async def list_all(self) -> Sequence[Instrument]: ...
  async def save(self, instrument: Instrument) -> None: ...
  ```

### 2. `DataSourceRepository`
- **Eski (Senkron):**
  ```python
  def get_by_id(self, source_id: DataSourceId) -> DataSource | None: ...
  def list_for_instrument(self, instrument_id: InstrumentId) -> Sequence[DataSource]: ...
  def save(self, source: DataSource) -> None: ...
  ```
- **Yeni (Asenkron):**
  ```python
  async def get_by_id(self, source_id: DataSourceId) -> DataSource | None: ...
  async def list_for_instrument(self, instrument_id: InstrumentId) -> Sequence[DataSource]: ...
  async def save(self, source: DataSource) -> None: ...
  ```

### 3. `CandleRepository`
- **Eski (Senkron, snapshot parametresi eksik):**
  ```python
  def get_candles(
      self,
      source_id: DataSourceId,
      timeframe: Timeframe,
      start_utc: datetime,
      end_utc: datetime,
      *,
      price_type: PriceType | None = None,
      as_of_utc: datetime | None = None,
      only_closed: bool = True,
  ) -> Sequence[Candle]: ...
  def save_candles(self, candles: Sequence[Candle]) -> int: ...
  ```
- **Yeni (Asenkron, bitemporal snapshot destekli):**
  ```python
  async def get_candles(
      self,
      source_id: DataSourceId,
      timeframe: Timeframe,
      start_utc: datetime,
      end_utc: datetime,
      *,
      price_type: PriceType | None = None,
      as_of_utc: datetime | None = None,
      knowledge_snapshot_time: datetime | None = None,
      only_closed: bool = True,
  ) -> Sequence[Candle]: ...
  async def save_candles(self, candles: Sequence[Candle]) -> int: ...
  ```

### 4. `MarketDataProvider`
- **Eski (Senkron, ilkel string obsession):**
  ```python
  @property
  def provider_id(self) -> str: ...
  def fetch_candles(
      self,
      symbol: str,
      timeframe: Timeframe,
      start_utc: datetime,
      end_utc: datetime,
  ) -> Sequence[Candle]: ...
  ```
- **Yeni (Asenkron, güçlü tip InstrumentId):**
  ```python
  @property
  def provider_id(self) -> ProviderId: ...
  async def fetch_candles(
      self,
      instrument_id: InstrumentId,
      timeframe: Timeframe,
      start_utc: datetime,
      end_utc: datetime,
  ) -> Sequence[Candle]: ...
  ```

---

## 7. SOLID VE CLEAN ARCHITECTURE DEĞERLENDİRMESİ

1. **Single Responsibility Principle (SRP):**
   - `Instrument`: Sadece kanonik enstrüman kimliğini ve matematiksel tick/pip standartlarını tanımlar.
   - `DataSource`: Belirli bir broker/sağlayıcı veri akışının kimliğini ve o akışa özgü mikro-yapı sapmalarını taşır.
   - `DatabaseSettings`: Sadece veritabanı bağlantı parametrelerini yönetir.
2. **Open/Closed Principle (OCP):**
   - Yeni brokerlar veya enstrümanlar eklendiğinde mevcut kod değiştirilmeden yeni `DataSource` ve `Instrument` tanımları enjekte edilebilir.
3. **Liskov Substitution Principle (LSP):**
   - `InstrumentRepository`, `CandleRepository`, `DataSourceRepository` ve `MarketDataProvider` protokollerini uygulayan sahte adapter'lar (`FakeInstrumentRepository`, `FakeCandleRepository` vb.) tüm sözleşmeleri istisnasız yerine getirmiştir.
4. **Interface Segregation Principle (ISP):**
   - Büyük ve monolitik bir repository yerine sorumluluklara göre ayrılmış portlar (`InstrumentRepository`, `CandleRepository`, `DataSourceRepository`) korunmuştur.
5. **Dependency Inversion Principle (DIP):**
   - Çekirdek ve domain katmanları somut hiçbir veritabanı veya ağ kütüphanesine bağımlı değildir; somut altyapı sınıfları domain protokollerine bağımlıdır.

---

## 8. DOMAIN / APPLICATION / INFRASTRUCTURE BAĞIMLILIK KONTROLÜ

Mimari AST denetimi (`tests/test_architecture.py`) ile doğrulanmıştır:
- Domain katmanı `traderlab.infrastructure`, `traderlab.markets`, `traderlab.interfaces`, `traderlab.agents`, `traderlab.application`, `traderlab.research` paketlerini kesinlikle import etmemektedir.
- Domain katmanı dış kütüphanelerden `sqlalchemy`, `psycopg`, `alembic`, `httpx`, `requests`, `fastapi`, `ccxt`, `openai` ve `pydantic_settings` paketlerini kesinlikle import etmemektedir.
- **Faz 0.5B ile Eklenen Güvence:** Domain katmanının `traderlab.core.config` modülünü import etmesi yasaklanmış ve test edilmiştir.

---

## 9. BROKER-SPESİFİK PIP / TICK / LOT MODELİ

Kanonik enstrüman özellikleri ile broker akış özellikleri ayrıştırılmıştır:

### Kanonik Enstrüman (`Instrument`)
- `tick_size: Decimal` — Piyasadaki en küçük fiyat adımı (ör. EURUSD için 0.00001, XAUUSD için 0.01).
- `pip_size: Decimal` — Standart pip boyutu (ör. EURUSD için 0.0001, XAUUSD için 0.10).
- `pip_value_ratio: Decimal` — `pip_size / tick_size` oranı (ör. EURUSD: 0.0001 / 0.00001 = 10 pipette = 1 pip).
- `price_precision: int` — Fiyat basamak sayısı.
- `lot_size: Decimal` — Minimum işlem veya kontrat birimi.

### Broker-Spesifik Akış (`DataSource`)
- `tick_size_override: Decimal | None`
- `pip_size_override: Decimal | None`
- `lot_size_override: Decimal | None`
- `price_precision_override: int | None`
- `typical_spread: Decimal | None`
- **Çözümleme Mantığı (`effective_*` metotları):**
  - Override değeri tanımlı ise o değer kullanılır.
  - Override değeri `None` ise kanonik `Instrument` değeri kullanılır.
  - Veri kaynağına ait olmayan bir enstrüman sorgulandığında fail-fast prensibiyle `ValueError` fırlatılır.

---

## 10. PRICETYPE VE CANDLE REGRESYON DEĞERLENDİRMESİ

Faz 0.5A ile kazanılan tüm finansal veri doğruluğu kuralları eksiksiz korunmuştur:
- `PriceType.BID`, `ASK`, `MID`, `UNKNOWN` zorunluluğu korunmaktadır.
- `UNKNOWN` fiyat türünün işlem fiyatlamasında kullanılamayacağı kuralı aktiftir.
- Bilinmeyen spread `None` kalmakta, asla sıfır varsayılmamaktadır.
- Sentetik veriler `is_synthetic=True` bayrağı ile işaretlenmektedir.
- Geometrik bar ortası (`range_midpoint`), gerçek piyasa spread ortasından (`mid_price`) ayrı tutulmaktadır.
- UTC normalizasyonu, `Decimal` hassasiyeti ve dondurulmuş (frozen) veri sınıfları korunmaktadır.

---

## 11. TEST SAYISI KARŞILAŞTIRMASI

| Test Grubu | Faz 0.5A Kapanış | Faz 0.5B Kapanış | Fark / Açıklama |
| :--- | :---: | :---: | :--- |
| `test_candle_pit_forex.py` | 25 | 27 | +2 (Tarihsel backfill ve bitemporal snapshot testleri) |
| `test_config.py` | 9 | 11 | +2 (`TestGetDbSettings` testleri) |
| `test_core_errors.py` | 11 | 11 | Değişiklik yok |
| `test_core_types.py` | 17 | 17 | Değişiklik yok |
| `test_domain_models.py` | 22 | 27 | +5 (`pip_size`, `effective_*` override ve hata testleri) |
| `test_domain_ports.py` | 0 | 8 | +8 (Yeni asenkron port sözleşme testleri) |
| `test_forex_instruments.py` | 3 | 3 | İçerik güçlendirildi (pip ve ratio assertions) |
| `test_architecture.py` | 2 | 2 | İçerik güçlendirildi (`core.config` yasağı eklendi) |
| `test_smoke.py` | 37+1 (38) | 38+1 (39) | +1 (`infrastructure.database.config` import testi) |
| **TOPLAM TEST SAYISI** | **126** | **144** | **+18 test artışı (Hiçbir test silinmedi/gevşetilmedi)** |

---

## 12. YENİ EKLENEN TESTLERİN LİSTESİ

1. `tests/test_candle_pit_forex.py::TestPointInTimeAndBitemporal::test_historical_backfill_research_estimated_allowed`
2. `tests/test_candle_pit_forex.py::TestPointInTimeAndBitemporal::test_bitemporal_snapshot_revision_filtering`
3. `tests/test_config.py::TestGetDbSettings::test_returns_db_settings`
4. `tests/test_config.py::TestGetDbSettings::test_cached`
5. `tests/test_domain_models.py::TestInstrument::test_invalid_pip_size`
6. `tests/test_domain_models.py::TestDataSource::test_effective_defaults`
7. `tests/test_domain_models.py::TestDataSource::test_effective_with_overrides`
8. `tests/test_domain_models.py::TestDataSource::test_instrument_mismatch_rejected`
9. `tests/test_domain_models.py::TestDataSource::test_invalid_overrides_rejected`
10. `tests/test_domain_ports.py::TestInstrumentRepositoryContract::test_protocol_conformance`
11. `tests/test_domain_ports.py::TestInstrumentRepositoryContract::test_save_and_retrieve`
12. `tests/test_domain_ports.py::TestDataSourceRepositoryContract::test_protocol_conformance`
13. `tests/test_domain_ports.py::TestDataSourceRepositoryContract::test_save_and_retrieve`
14. `tests/test_domain_ports.py::TestCandleRepositoryContract::test_protocol_conformance`
15. `tests/test_domain_ports.py::TestCandleRepositoryContract::test_save_and_query_bitemporal`
16. `tests/test_domain_ports.py::TestMarketDataProviderContract::test_protocol_conformance`
17. `tests/test_domain_ports.py::TestMarketDataProviderContract::test_fetch_candles`
18. `tests/test_smoke.py::test_package_imports[traderlab.infrastructure.database.config]`

---

## 13. PYTEST GERÇEK SONUÇLARI

### Tam Test Koşumu Çıktısı:
```text
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-8.4.2, pluggy-1.7.0 -- C:\trader\traderLab\traderlab\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\trader\traderLab\traderlab
configfile: pyproject.toml
plugins: asyncio-0.26.0
asyncio: mode=Mode.STRICT, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 144 items

tests/test_architecture.py ..                                            [  1%]
tests/test_candle_pit_forex.py ...........................               [ 20%]
tests/test_config.py ...........                                         [ 27%]
tests/test_core_errors.py ...........                                    [ 35%]
tests/test_core_types.py .................                               [ 47%]
tests/test_domain_models.py ...........................                  [ 65%]
tests/test_domain_ports.py ........                                      [ 71%]
tests/test_forex_instruments.py ...                                      [ 73%]
tests/test_smoke.py .......................................              [100%]

============================= 144 passed in 0.30s =============================
```

### Pytest Collect-Only Çıktısı:
```text
144 tests collected in 0.14s
```

---

## 14. RUFF VE MYPY GERÇEK SONUÇLARI

### Ruff Check Sonucu:
```text
All checks passed!
```

### Ruff Format Check Sonucu:
```text
49 files already formatted
```

### Mypy Statik Tip Kontrolü Sonucu:
```text
Success: no issues found in 38 source files
```

---

## 15. BAŞARISIZ OLAN VE SONRADAN DÜZELTİLEN TESTLER

1. **`test_revision_ingested_after_as_of_rejected_to_prevent_leakage` (Faz 0.5A kalıntısı):**
   - *Hata:* Faz 0.5A'daki test, `ingested_at > as_of` durumunda `RESEARCH_ESTIMATED` modunun da `False` döndürmesini bekliyordu. Bu kural tarihsel backfill kullanımını engelliyordu.
   - *Çözüm:* Test, `decision_time` ile `knowledge_snapshot_time` ayrımını doğrulayacak şekilde güncellendi. Snapshot belirtilmediğinde tarihsel backfill'e izin verildiği; snapshot belirtildiğinde revizyonun filtrelendiği doğrulandı.
2. **`test_domain_ports.py` Fixture Eksikliği:**
   - *Hata:* `xau_id` ve `source_id` fixture'ları `conftest.py` yerine başka test dosyasında tanımlı olduğu için port testlerinde setup error verdi.
   - *Çözüm:* İlgili testler kendi bağımsız kimlik nesnelerini oluşturacak şekilde düzenlendi ve hata giderildi.
3. **`test_domain_models.py` Eksik Tip Importu:**
   - *Hata:* `ProviderId` tipi import edilmediği için NameError verdi.
   - *Çözüm:* `traderlab.core.types` import bloğuna `ProviderId` eklendi.

---

## 16. KALAN TEKNİK RİSKLER

1. **Bellek İçi PIT vs Veritabanı PIT:**
   - `Candle.is_available_at` fonksiyonu domain düzeyinde bitemporal filtreleme kurallarını eksiksiz uygular. Ancak milyonlarca satırlık veritabanı sorgularında bu filtrelemenin SQL seviyesinde (`WHERE ingested_at <= snapshot_time AND available_at <= decision_time`) indeks destekli olarak yapılması gerekir. Gerçek SQL performansı ve PIT tutarlılığı Faz 1'de doğrulanacaktır.
2. **Gecikme Modelleri:**
   - Tarihsel verilerde `available_at` bilgisi bulunmadığında `close_time` baz alınmaktadır. Gerçek canlı işlemlerde broker/gateway yayın gecikmesi (ör. 50ms - 2000ms) söz konusu olabilir. Araştırmalarda muhafazakâr bir tampon eklenmesi strateji aşamasında dikkate alınmalıdır.

---

## 17. HENÜZ UYGULANMAYAN ÖZELLİKLER (SINIRLAR KORUNDU)

- PostgreSQL tabloları ve Alembic migration scriptleri oluşturulmamıştır.
- Canlı piyasa verisi indirme mekanizması çalıştırılmamıştır.
- Broker API (MetaTrader, OANDA vb.) entegrasyonu kurulmamıştır.
- Strateji, indikatör, backtest motoru veya LLM agent kodu yazılmamıştır.

---

## 18. FAZ 0.5C İÇİN ÖNERİLEN KONTROL BAŞLIKLARI

1. **Application Layer Use Case Sözleşmeleri:** Veri indirme (`IngestionUseCase`) ve analiz pipeline orkestrasyonu sözleşmelerinin asenkron portlara bağlanması.
2. **Altyapı Provider Taslakları:** CCXT, MetaTrader veya harici API adaptör taslaklarının async port sözleşmelerine bağlanması.
3. **Multi-Timeframe Hizalama Doğrulaması:** M1, M5, H1, D1 zaman dilimleri arasındaki mum tamamlama ve PIT kurallarının netleştirilmesi.

---

## 19. FAZ 0.5B KABUL KRİTERLERİNİN TEK TEK DURUMU

| No | Kabul Kriteri | Durum | Doğrulama Kanıtı |
| :---: | :--- | :---: | :--- |
| 1 | `decision_time` ve `knowledge_snapshot_time` ayrımı | **GEÇTİ** | `Candle.is_available_at` & `test_candle_pit_forex.py` |
| 2 | `STRICT_PIT` doğrulanmış canlı PIT güvencesi | **GEÇTİ** | `test_unverified_confidence_rejected_under_strict_pit` |
| 3 | `RESEARCH_ESTIMATED` tarihsel backfill desteği | **GEÇTİ** | `test_historical_backfill_research_estimated_allowed` |
| 4 | Bitemporal revizyon sızıntısı engelleme | **GEÇTİ** | `test_bitemporal_snapshot_revision_filtering` |
| 5 | Asenkron I/O Port Protokolleri | **GEÇTİ** | `domain/ports.py` (`async def`) & `test_domain_ports.py` |
| 6 | Altyapı konfigürasyon ayrımı (`DatabaseSettings`) | **GEÇTİ** | `infrastructure/database/config.py` & `test_config.py` |
| 7 | Domain konfigürasyon ve altyapı izolasyonu | **GEÇTİ** | `test_architecture.py` (AST boundary test) |
| 8 | Broker-spesifik pip/tick/lot metadata | **GEÇTİ** | `Instrument.pip_size` & `DataSource.effective_*` |
| 9 | Forex kanonik enstrüman güncellemeleri | **GEÇTİ** | `markets/forex/instruments.py` & `test_forex_instruments.py` |
| 10 | ADR tarih doğruluğu (2026) ve yeni kayıtlar | **GEÇTİ** | `docs/decisions/adr-log.md` (ADR-001..ADR-010) |
| 11 | Test envanterinin korunması ve artırılması | **GEÇTİ** | 126 testten 144 teste yükseltildi |
| 12 | Kod kalitesi (Ruff lint, format ve Mypy) | **GEÇTİ** | 0 lint error, 49 formatted, 0 mypy error |

---

## NİHAİ KABUL DURUMU

# **`READY`**

Faz 0.5B kapsamındaki tüm mimari, domain, bitemporal PIT, asenkron port ve altyapı ayrımı gereksinimleri eksiksiz tamamlanmış, otomatik testler ve statik analizlerle doğrulanmıştır.

Kullanıcı onayı verilene kadar ilerleme durdurulmuştur. Faz 0.5C'ye geçilmemiştir.
