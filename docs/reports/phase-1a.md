# TRADERLAB — FAZ 1A TESLİM RAPORU
## PostgreSQL Veri Mimarisi, Bitemporal Saklama ve Entegrasyon Testleri

**Tarih:** 2026-10-20  
**Mühendislik Rolü:** Principal Software Architect, Senior Quant Developer, PostgreSQL Database Engineer & Financial Data Infrastructure Engineer  
**Kapsam:** PostgreSQL Şeması, Alembic Migration Altyapısı, `PostgresCandleRepository`, Bitemporal PIT Sorguları ve Gerçek Veritabanı Entegrasyon Testleri  
**Nihai Karar:** **`READY`**

---

## 1. YÖNETİCİ ÖZETİ VE MİMARİ KABUL DURUMU

Faz 1A kapsamında, TRADERLAB araştırma platformunun finansal zaman serisi saklama omurgası gerçek PostgreSQL veritabanı üzerinde kurulmuş, bitemporal Point-in-Time (PIT) sorgulama mantığı SQL seviyesinde doğrulanmış ve tüm altyapı bileşenleri bağımsız entegrasyon testleriyle güvence altına alınmıştır.

- **Mevcut Test Durumu:** Faz 0.5C sonunda devralınan **147 testin tamamı korunmuş**, eklenen **13 gerçek PostgreSQL entegrasyon testi** ile toplam test sayısı **160**'a yükselmiştir (%100 Başarı).
- **Gerçek Veritabanı Doğrulaması:** Mock/fake adaptörler yerine yerel PostgreSQL 14 üzerinde izole `traderlab_test` veritabanı kullanılmış; DDL, bitemporal snapshot, ACID rollback, Decimal hassasiyeti ve tekilleştirme gerçek PostgreSQL üzerinde kanıtlanmıştır.
- **Nihai Kabul Kararı:** **`READY`** (Faz 1A kapsamındaki tüm hedefler eksiksiz tamamlanmış, teknik borç bırakılmamıştır).

---

## 2. GÜVENLİK VE PROJE SINIRLARI DOĞRULAMASI

1. **İzolasyon Garantisi:**
   - Eski `borsa/` dizinine ve `trader_muhendis` veritabanına **kesinlikle dokunulmamıştır**.
   - Yalnızca `traderlab/` projesi içinde çalışılmıştır.
2. **Hedef Veritabanı Beyaz Listesi (Whitelist) & Fail-Fast Mekanizması:**
   - Üretim veya yabancı veritabanlarına yanlışlıkla bağlanılmasını engellemek amacıyla hem Pydantic seviyesinde ([`DatabaseSettings`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/config.py#L34-L41)), hem async motor seviyesinde ([`create_db_engine`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/session.py#L40-L46)), hem de migration seviyesinde ([`migrations/env.py`](file:///c:/trader/traderLab/traderlab/migrations/env.py#L48-L55)) `PERMITTED_DATABASES = frozenset({"traderlab", "traderlab_test"})` kuralı uygulanmıştır.
   - Veritabanı adında `muhendis` veya `borsa` geçen veya izin verilmeyen herhangi bir isim tespit edildiğinde sistem anında `ConfigurationError` fırlatarak fail-fast uygular. Bu davranış entegrasyon testleriyle kanıtlanmıştır ([`test_fail_fast_on_forbidden_database`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L552-L566), [`test_forbidden_database_migration_rejected`](file:///c:/trader/traderLab/traderlab/tests/integration/test_alembic_migrations.py#L84-L90)).
3. **Gizlilik:**
   - Hiçbir şifre veya `.env` içeriği loglanmamıştır. DSN gösterimlerinde şifre `***` olarak maskelenmektedir.

---

## 3. POSTGRESQL ŞEMA TASARIMI VE SÜRÜMLEME STRATEJİSİ

### A. Sürümleme Stratejisi Analizi: Append-Only vs Çift Tablo
Yapılan mimari değerlendirmede iki ayrı tablo (`candles` + `candle_revisions`) yerine **tekil append-only tablo (`candles`)** mimarisi seçilmiştir ([ADR-011](file:///c:/trader/traderLab/traderlab/docs/decisions/adr-log.md#L303-L330)):

- **Neden Append-Only Tek Tablo?**
  1. İki ayrı tablo yaklaşımı iki aşamalı yazma (two-phase write) veya veritabanı trigger'ı gerektirir. Transaction çökmesinde aktif tablo ile arşiv tablosunun senkronizasyonu bozulabilir (split-brain drift).
  2. Append-only modelde her veri düzeltmesi doğrudan `revision` alanı artırılarak kaydedilir. Hiçbir geçmiş kayıt `UPDATE` veya `DELETE` ile ezilmez.
  3. `DISTINCT ON (open_time, price_type) ... ORDER BY open_time, price_type, revision DESC` deseni sayesinde PostgreSQL, oluşturulan kompozit indeks üzerinden tek bir sorguda en güncel geçerli versiyonu logaritmik hızda ($O(\log N)$) çeker.

### B. Tablo Şeması (DDL)
[`migrations/versions/0001_create_candles_table.py`](file:///c:/trader/traderLab/traderlab/migrations/versions/0001_create_candles_table.py) ile oluşturulan tablo şeması:

```sql
CREATE TABLE candles (
    instrument_id VARCHAR(32) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    timeframe INT NOT NULL,
    open_time TIMESTAMPTZ NOT NULL,
    price_type VARCHAR(16) NOT NULL,
    revision INT NOT NULL DEFAULT 0,
    close_time TIMESTAMPTZ NOT NULL,
    open NUMERIC(18, 8) NOT NULL,
    high NUMERIC(18, 8) NOT NULL,
    low NUMERIC(18, 8) NOT NULL,
    close NUMERIC(18, 8) NOT NULL,
    volume NUMERIC(18, 4),
    tick_volume NUMERIC(18, 4),
    spread NUMERIC(18, 8),
    is_closed BOOLEAN NOT NULL DEFAULT TRUE,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ,
    available_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    availability_confidence VARCHAR(16) NOT NULL,
    CONSTRAINT pk_candles PRIMARY KEY (instrument_id, source_id, timeframe, open_time, price_type, revision)
);

-- Point-in-Time ve Snapshot Hızlı Arama İndeksi
CREATE INDEX idx_candles_pit_lookup ON candles (
    source_id, timeframe, price_type, open_time, ingested_at, available_at
);

-- Enstrüman Bazlı Filtreleme İndeksi
CREATE INDEX idx_candles_instrument_tf ON candles (
    instrument_id, timeframe, open_time
);
```

---

## 4. BITEMPORAL PIT SORGULARI VE MATEMATİKSEL GARANTİLER

PostgreSQL repository seviyesinde ([`PostgresCandleRepository.get_candles`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/repositories/candle_repository.py#L150-L248)) 4 farklı zaman ekseni net olarak ayrılmıştır:

1. **`event_time` (`open_time`, `close_time`):** Finansal piyasa olayının gerçekleştiği fiziksel zaman aralığı.
2. **`source_availability_time` (`available_at`):** Verinin sağlayıcı ağında hazır hale geldiği ve kararda kullanılabileceği an.
3. **`decision_time` (`as_of_utc`):** Simüle edilen alım-satım kararının alındığı milisaniye.
4. **`system_knowledge_time` (`ingested_at` / `knowledge_snapshot_time`):** Verinin veritabanına kalıcı olarak yazıldığı fiziksel duvar saati zamanı.

### PIT Sorgu Mantığı (SQL İfadesi)
SQLAlchemy 2.1 extension'ı `distinct_on` kullanılarak derlenen optimize sorgu:

```sql
SELECT DISTINCT ON (open_time, price_type) *
FROM candles
WHERE source_id = :source_id
  AND timeframe = :timeframe
  AND open_time >= :start_utc AND open_time < :end_utc
  AND is_closed = TRUE
  AND (:price_type IS NULL OR price_type = :price_type)
  AND (
    -- STRICT_PIT: Sadece doğrulanmış ve karar anından önce hazır veriler
    (:policy = 'STRICT_PIT' AND availability_confidence = 'VERIFIED' AND available_at IS NOT NULL AND available_at <= :as_of_utc)
    -- RESEARCH_ESTIMATED: Tarihsel araştırmada kapanış zamanı veya tahmini erişilebilirlik
    OR (:policy = 'RESEARCH_ESTIMATED' AND COALESCE(available_at, close_time) <= :as_of_utc)
  )
  -- Kapanmamış mumu karar anında engelle
  AND (close_time <= :as_of_utc)
  -- Bilgi snapshot filtrelemesi (Hindsight Leakage Kalkanı)
  AND (ingested_at <= :effective_snapshot)
ORDER BY open_time, price_type, revision DESC;
```

---

## 5. FİNANSAL VERİ DOĞRULUĞU VE MİKRO-YAPI GÜVENCELERİ

1. **Açık Fiyat Türü Zorunluluğu (`price_type`):**
   - [`MarketDataProvider.fetch_candles`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/ports.py#L91-L99) portundaki varsayılan `PriceType.BID` parametresi kaldırılmış; çağrıyı yapanların `price_type: PriceType` parametresini açıkça belirtmesi zorunlu kılınmıştır. Yanlışlıkla BID varsayımı yapılması mimari düzeyde engellenmiştir.
2. **Hacim Ayrımı (Bilinmeyen Hacim vs 0 Hacim):**
   - Forex piyasalarında borsa tescilli gerçek işlem hacmi (real volume) genellikle bulunmaz. `Candle.volume` alanı nullable `Decimal | None` yapılarak:
     - `volume = None`: Hacim bilinmiyor (unknown).
     - `volume = Decimal("0")`: Mum periyodunda piyasada 0 işlem gerçekleşti.
     - `tick_volume = Decimal(...)`: Broker fiyat güncelleme sıklığı.
   - Bu ayrım hem Python domain nesnesinde hem de PostgreSQL şemasında (`volume NUMERIC(18, 4) NULL`) korunmuştur ([`test_null_spread_and_unknown_volume`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L468-L525)).
3. **Hassas Spread Modeli:**
   - `spread` alanı `Numeric(18, 8)` ve nullable tanımlanmıştır. Bilinmeyen spread asla 0 kabul edilmez (`spread=None`).

---

## 6. REPOSITORY IMPLEMENTASYONU (`PostgresCandleRepository`)

[`src/traderlab/infrastructure/database/repositories/candle_repository.py`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/repositories/candle_repository.py):

- **İdempotent Yazma (`save_candles`):**
  - PostgreSQL `insert(CandleModel).on_conflict_do_nothing(...).returning(CandleModel.instrument_id)` mekanizması ile aynı verinin tekrar gönderilmesi durumunda veritabanı hata vermez, mükerrer kayıt oluşturmaz ve eklenen satır sayısını 0 olarak döner.
- **Sessiz Veri Kaybını Önleme (Collision Revision Bump):**
  - Aynı doğal anahtarda (`instrument`, `source`, `timeframe`, `open_time`, `price_type`) farklı OHLCV verisi geldiğinde, eski verinin üzerine yazılmaz; sistem bunu otomatik olarak `revision = max_revision + 1` olarak yeni bir sürümle kalıcı hale getirir.
- **UTC Zaman Dilimi Normalizasyonu:**
  - Veritabanı motoru `connect_args={"options": "-c timezone=utc"}` parametresiyle başlatılarak PostgreSQL oturumunun `UTC` çalışması teminat altına alınmıştır.
  - Veritabanından okunan tüm zaman damgaları [`_to_utc`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/models.py#L41-L47) fonksiyonu ile `dt.astimezone(UTC)` üzerinden normalize edilerek domain modeline aktarılır.

---

## 7. GERÇEK POSTGRESQL ENTEGRASYON TESTLERİ

Tüm entegrasyon testleri yerel PostgreSQL 14 üzerinde `traderlab_test` veritabanında çalıştırılmıştır:

| Test Sınıfı & Metodu | Test Edilen Mimari Davranış | Sonuç |
| :--- | :--- | :---: |
| [`TestAlembicMigrations::test_schema_objects_exist_after_migration`](file:///c:/trader/traderLab/traderlab/tests/integration/test_alembic_migrations.py#L23) | Alembic migration tabloları, composite PK ve indexleri eksiksiz oluşturur | **GEÇTİ** |
| [`TestAlembicMigrations::test_forbidden_database_migration_rejected`](file:///c:/trader/traderLab/traderlab/tests/integration/test_alembic_migrations.py#L84) | Yetkisiz veritabanına (`trader_muhendis`) migration çalıştırma talebi engellenir | **GEÇTİ** |
| [`TestPostgresCandleRepository::test_save_and_retrieve_single_candle`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L35) | Temel persist, UTC zaman damgası ve Decimal hassasiyet round-trip doğrulaması | **GEÇTİ** |
| [`TestPostgresCandleRepository::test_multi_instrument_and_source_isolation`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L90) | XAUUSD ve EURUSD, feed-alpha ve feed-beta sorgularının tam izolasyonu | **GEÇTİ** |
| [`TestPostgresCandleRepository::test_simultaneous_bid_and_ask_storage`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L148) | Aynı anda gelen BID ve ASK barlarının çakışmadan saklanması ve filtrelenmesi | **GEÇTİ** |
| [`TestPostgresCandleRepository::test_idempotent_duplicate_storage`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L217) | Aynı mumun iki kez kaydedilmesi; 0 yeni satır dönmesi (idempotent no-op) | **GEÇTİ** |
| [`TestPostgresCandleRepository::test_data_correction_as_new_revision_and_snapshot_query`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L257) | Düzeltilmiş verinin rev=1 olarak saklanması; eski snapshot'ın rev=0 dönmesi | **GEÇTİ** |
| [`TestPostgresCandleRepository::test_strict_pit_verified_confidence_required`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L340) | STRICT_PIT modunda ESTIMATED/UNKNOWN barların kesin reddi | **GEÇTİ** |
| [`TestPostgresCandleRepository::test_research_estimated_historical_backfill`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L389) | RESEARCH_ESTIMATED modunda `available_at=None` tarihsel backfill izni | **GEÇTİ** |
| [`TestPostgresCandleRepository::test_rejection_of_unclosed_candle_at_decision_time`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L427) | 10:00-11:00 H1 mumunun 10:15 kararında reddedilmesi | **GEÇTİ** |
| [`TestPostgresCandleRepository::test_null_spread_and_unknown_volume`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L468) | Bilinmeyen hacim (`None`) ile 0 hacim (`Decimal(0)`) ayrımının doğrulanması | **GEÇTİ** |
| [`TestPostgresCandleRepository::test_transaction_rollback`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L526) | Hata durumunda session rollback çalışması ve yetim kayıt bırakılmaması | **GEÇTİ** |
| [`TestPostgresCandleRepository::test_fail_fast_on_forbidden_database`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L552) | Yasaklı DB adıyla engine açma girişiminde fail-fast (`ConfigurationError`) | **GEÇTİ** |

---

## 8. TEST ENVANTERİ VE GELİŞİMİ

- **Faz 0.5C Bitiş Test Sayısı:** 147 test
- **Faz 1A Eklenen Test Sayısı:** +13 gerçek PostgreSQL entegrasyon testi
- **Güncel Toplam Test Sayısı:** **160 test**

### Dosya Bazında Test Dağılımı

| Test Dosyası | Test Kategorisi | Adet | Durum |
| :--- | :--- | :---: | :---: |
| [`tests/test_architecture.py`](file:///c:/trader/traderLab/traderlab/tests/test_architecture.py) | AST Mimari Sınır / Bağımlılık Testleri | 2 | GEÇTİ |
| [`tests/test_candle_pit_forex.py`](file:///c:/trader/traderLab/traderlab/tests/test_candle_pit_forex.py) | Point-in-Time & Forex Microstructure Unit | 28 | GEÇTİ |
| [`tests/test_config.py`](file:///c:/trader/traderLab/traderlab/tests/test_config.py) | Konfigürasyon ve DSN Unit | 10 | GEÇTİ |
| [`tests/test_core_errors.py`](file:///c:/trader/traderLab/traderlab/tests/test_core_errors.py) | Hata Hiyerarşisi Unit | 11 | GEÇTİ |
| [`tests/test_core_types.py`](file:///c:/trader/traderLab/traderlab/tests/test_core_types.py) | Tipler, Enumlar, UTC Doğrulama Unit | 17 | GEÇTİ |
| [`tests/test_domain_models.py`](file:///c:/trader/traderLab/traderlab/tests/test_domain_models.py) | Varlık ve Değer Nesneleri Unit | 27 | GEÇTİ |
| [`tests/test_domain_ports.py`](file:///c:/trader/traderLab/traderlab/tests/test_domain_ports.py) | Async Port Sözleşmeleri Unit | 10 | GEÇTİ |
| [`tests/test_forex_instruments.py`](file:///c:/trader/traderLab/traderlab/tests/test_forex_instruments.py) | Kanonik Forex Enstrümanları Unit | 3 | GEÇTİ |
| [`tests/test_smoke.py`](file:///c:/trader/traderLab/traderlab/tests/test_smoke.py) | Paket İçe Aktarma Smoke Testleri | 39 | GEÇTİ |
| [`tests/integration/test_alembic_migrations.py`](file:///c:/trader/traderLab/traderlab/tests/integration/test_alembic_migrations.py) | Alembic Schema & Migration Integration | 2 | GEÇTİ |
| [`tests/integration/test_postgres_candle_repository.py`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py) | PostgreSQL Bitemporal PIT Repository Integration | 11 | GEÇTİ |
| **GENEL TOPLAM** | **Tüm Testler** | **160** | **Eksiksiz Başarılı** |

---

## 9. OTOMATİK KALİTE KONTROL ÇIKTILARI

```bash
# 1. Pytest Test Koşumu
python -m pytest -v --tb=short
=> 160 passed in 1.15s

# 2. Pytest Test Koleksiyonu
python -m pytest --collect-only -q
=> 160 tests collected in 0.52s

# 3. Ruff Linter
ruff check src/ tests/
=> All checks passed!

# 4. Ruff Formatter
ruff format --check src/ tests/
=> 57 files already formatted

# 5. Mypy Statik Tip Denetimi
mypy src/traderlab/
=> Success: no issues found in 42 source files

# 6. AST Mimari Sınır Testi
python -m pytest tests/test_architecture.py -v
=> 2 passed in 0.02s
```

---

## 10. FAZ 1A SÜRESİNCE TESPİT EDİLEN VE ÇÖZÜLEN SORUNLAR

1. **Windows Psycopg 3 Async Event Loop Uyumu:**
   - *Sorun:* Windows platformunda Python 3.11 varsayılan `ProactorEventLoop` kullanır. Psycopg 3 asenkron modda bu loop türünü desteklemez (`InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`).
   - *Çözüm:* [`session.py`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/session.py#L20-L24), [`env.py`](file:///c:/trader/traderLab/traderlab/migrations/env.py#L11-L13) ve [`conftest.py`](file:///c:/trader/traderLab/traderlab/tests/conftest.py#L10-L14) içine Windows tespit edildiğinde `WindowsSelectorEventLoopPolicy` ayarlayan platform koruması eklendi.
2. **PostgreSQL Yerel Sunucu Zaman Dilimi Sapması:**
   - *Sorun:* Yerel Windows PostgreSQL sunucusu Türkiye saat diliminde (`Europe/Istanbul` UTC+03:00) çalıştığı için sorgulanan `TIMESTAMPTZ` değerleri `+03:00` ofsetiyle dönmekte, bu da katı UTC kontrolümüzü tetiklemekteydi.
   - *Çözüm:* Engine bağlantı parametrelerine `connect_args={"options": "-c timezone=utc"}` eklendi; ayrıca ORM'den domaine dönüşte [`_to_utc()`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/models.py#L41-L47) ile `astimezone(UTC)` normalizasyonu sağlandı.
3. **Async Insert Rowcount Belirsizliği:**
   - *Sorun:* Psycopg 3 sürücüsünde `insert(...).on_conflict_do_nothing(...)` sorgusunda `res.rowcount` değeri `-1` dönüyordu.
   - *Çözüm:* Sorguya `.returning(CandleModel.instrument_id)` eklenerek `res.scalar_one_or_none()` ile eklenen satırlar deterministik ve kesin olarak tespit edildi.
4. **SQLAlchemy 2.1 `distinct_on` Deprecation:**
   - *Sorun:* SQLAlchemy 2.1'de `select(...).distinct(expr)` kullanımı deprecation uyarısı veriyordu (`filterwarnings = ["error"]` nedeniyle hata oluyordu).
   - *Çözüm:* SQLAlchemy 2.1'in resmi yeni API'si olan `select(...).ext(distinct_on(...))` kullanımına geçildi.

---

## 11. KALAN TEKNİK RİSKLER

1. **Ağ İletim Gecikmesi (Network Latency):** Canlı broker bağlantısı kurulana kadar gerçek ağ dalgalanmaları (50ms–2000ms) simüle edilemez; bu Faz 1B ve Faz 4'ün konusudur.
2. **Büyük Veri Partitioning İhtiyacı:** Şu anki `idx_candles_pit_lookup` indeksi milyonlarca bar için yeterlidir. On milyonlarca bar seviyesine çıkıldığında PostgreSQL 14+ `PARTITION BY RANGE (open_time)` (ör. yıllık veya çeyreklik partition) düşünülmelidir.

---

## 12. FAZ 1B HAZIRLIK VE ÖN KOŞULLAR

Faz 1B (Veri Toplama Pipeline'ı, Tarihsel Backfill CLI ve XAUUSD Veri Sağlayıcı Adaptörü) için teknik zemin hazırdır:
1. `MarketDataProvider` portuna bağlanacak ilk somut adaptör (ör. simüle CSV vendor adaptörü veya OANDA REST adaptörü) yazılabilir.
2. CLI üzerinden idempotent ingestion komutları (`traderlab ingest --instrument XAUUSD --timeframe H1 ...`) geliştirilebilir.
3. Aralıklı veri (gap) tespit ve doldurma (backfill) algoritması `application/ingestion` katmanında use case olarak kurgulanabilir.

---

## 13. TALİMAT GEREĞİ DURDURULDU

Mimari denetim, veritabanı şeması, migration'lar, repository kodu ve gerçek PostgreSQL entegrasyon testleri eksiksiz başarıyla tamamlanmıştır.

### Mimari Kabul Kararı:
# **`READY`**

> [!IMPORTANT]
> **Talimatınız gereği çalışma burada durdurulmuştur.**  
> Faz 1B'ye otomatik geçiş yapılmamıştır. Faz 1B veri pipeline ve ingestion geliştirme çalışmalarına başlamak için kullanıcı onayı beklenmektedir.
