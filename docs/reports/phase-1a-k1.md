# TRADERLAB — FAZ 1A-K1 TEKNİK DENETİM VE KABUL RAPORU
## PostgreSQL Veri Bütünlüğü ve Kritik Regresyon Testleri

**Tarih:** 2026-10-20  
**Mühendislik Rolü:** Senior Quantitative Software Engineer, PostgreSQL Database Engineer & Software Quality Auditor  
**Kapsam:** Veri Bütünlüğü, Eşzamanlı Revizyon Yarış Koşulları, Enstrüman/Sağlayıcı İzolasyonu, Bitemporal PIT ve SQL Performans Denetimi  
**Nihai Karar:** **`READY`**

---

## 1. BAŞLANGIÇ TEST SONUÇLARI VE ÇALIŞMA DURUMU

Faz 1A devralma raporunda belirtilen 160 testlik temel küme bağımsız olarak yeniden doğrulanmıştır:
- **Başlangıç Test Sayısı:** 160 test (147 birim/kontrat/smoke + 13 PostgreSQL entegrasyon testi).
- **Yürütme Süresi:** 1.15 saniye.
- **Sonuç:** 160 başarılı, 0 başarısız (%100 Başarı).

Çalışma ortamı:
- PostgreSQL 14 (Port: 5432, Kullanıcı: `postgres`, Hedef DB: `traderlab_test`).
- Python 3.11.9, SQLAlchemy 2.0.44 (psycopg3 async engine), Alembic 1.17.1.
- `borsa/` ve `trader_muhendis` veritabanlarına kesinlikle dokunulmamış, yalnızca izole `traderlab_test` ortamı kullanılmıştır.

---

## 2. İNCELENEN KAYNAK KODLARI

| Bileşen / Katman | Dosya Yolu | İnceleme Amacı |
| :--- | :--- | :--- |
| **Önceki Rapor** | [`docs/reports/phase-1a.md`](file:///c:/trader/traderLab/traderlab/docs/reports/phase-1a.md) | Faz 1A şema, DDL ve test kabul iddialarının teyidi |
| **Mimari Kararlar** | [`docs/decisions/adr-log.md`](file:///c:/trader/traderLab/traderlab/docs/decisions/adr-log.md) | ADR-004, ADR-011 bitemporal append-only ve PIT kararları |
| **PostgreSQL Repository** | [`src/traderlab/infrastructure/database/repositories/candle_repository.py`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/repositories/candle_repository.py) | Eşzamanlı revizyon, kilit mekanizması ve DISTINCT ON analizi |
| **SQLAlchemy ORM Model** | [`src/traderlab/infrastructure/database/models.py`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/models.py) | `CandleModel`, birleşik PK ve indeks yapıları |
| **Alembic Migration** | [`migrations/versions/0001_create_candles_table.py`](file:///c:/trader/traderLab/traderlab/migrations/versions/0001_create_candles_table.py) | DDL kısıtları, `pk_candles`, `idx_candles_pit_lookup` |
| **Domain Sözleşmesi** | [`src/traderlab/domain/ports.py`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/ports.py) | `CandleRepository` ve `MarketDataProvider` protokol imzaları |
| **Mevcut Entegrasyon Testleri**| [`tests/integration/test_postgres_candle_repository.py`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py) | Gerçek PostgreSQL davranış ve bitemporal testleri |

---

## 3. KRİTİK KONTROL A — EŞZAMANLI VERİ REVİZYONU VE YARIŞ KOŞULLARI

### A. Tespit Edilen Kritik Problem (Silent Data Loss & Redundant Revisions)
Mevcut `save_candles` fonksiyonunda iki kritik zaaf tespit edilmiş ve reprodüksiyon testiyle kanıtlanmıştır:

1. **Eşzamanlı Revizyon Yarış Koşulu (`MAX(revision) + 1` Race):**
   - İki bağımsız async oturum aynı anda var olan bir muma farklı fiyat düzeltmeleri (Worker A: `close=2312`, Worker B: `close=2316`) kaydetmek istediğinde:
   - Her iki oturum da veritabanından mevcut en yüksek revizyonu `0` olarak okur.
   - İkisi de `target_revision = 1` hesaplar.
   - Worker A satırı yazar (`revision=1`).
   - Worker B satırı yazarken `ON CONFLICT DO NOTHING` ile karşılaşır ve satır **sessizce çöpe atılır**. Worker B'nin fiyat düzeltmesi veritabanından kalıcı olarak kaybolur. Veritabanında 3 satır olması gerekirken 2 satır kalır.
2. **Gereksiz Mükerrer Revizyon (Redundant Revision Bug):**
   - Daha önce revizyon 0 ve revizyon 1 kaydedilmiş bir muma, dış bir kaynaktan revizyon 1 ile birebir aynı veriye sahip bir mum `revision=0` varsayılanıyla tekrar gönderildiğinde; döngüdeki `row.revision == target_revision` kontrolü `1 == 0` (False) olduğundan sistem verinin değiştiğini varsayıp `revision=2` oluşturmaktaydı.

### B. Uygulanan Çözüm
- **PostgreSQL Transactional Advisory Lock:**  
  Her mumun doğal anahtarı (`instrument_id:source_id:timeframe:open_time:price_type`) üzerinden `hashtext` ile türetilen anahtarla `pg_advisory_xact_lock(hashtext(lock_key))` kilidi alınmıştır.
  - Kilit tabloyu değil, yalnızca ilgili mumu kilitler; bağımsız mumlar tam paralellikte çalışmaya devam eder.
  - Kilit transaction bazlıdır (`xact`); transaction commit veya rollback anında veritabanı tarafından otomatik olarak serbest bırakılır.
  - İki işçi aynı mumu güncellemek istediğinde Worker B, Worker A'nın commit etmesini bekler. Worker A bittiğinde Worker B en güncel durumu (rev=1) okur ve güvenle `rev=2` olarak kaydeder. Sıfır veri kaybı!
- **Global Kilit Sıralaması (Dijkstra Resource Hierarchy - Deadlock Önleme):**  
  Toplu (`Sequence[Candle]`) kayıt işlemlerinde concurrent oturumların ters sırada mum kilitlemesi (`[A, B]` vs `[B, A]`) sonucu oluşabilecek deadlock'ları engellemek için `candles` listesi kilit öncesinde doğal anahtarına göre deterministik olarak sıralanmaktadır (`sorted_candles`).
- **Veri Eşitlik Kontrolü (`_is_candle_data_identical`):**  
  Veritabanındaki en son revizyonun OHLCV değerleri ile gelen mumun değerleri birebir aynıysa, gereksiz revizyon artırımı yapılmaz ve işlem idempotent no-op olarak atlanır.

İlgili Testler:
- [`TestPostgresCandleRepository::test_concurrent_revisions_no_silent_loss`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L576-L669) (**GEÇTİ**)
- [`TestPostgresCandleRepository::test_repeated_identical_data_does_not_create_redundant_revision`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L671-L745) (**GEÇTİ**)
- [`TestPostgresCandleRepository::test_concurrent_batch_deadlock_prevention`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L1040-L1110) (**GEÇTİ**)

---

## 4. KRİTİK KONTROL B — ENSTRÜMAN VE VERİ SAĞLAYICISI İZOLASYONU

### A. Mevcut Durum ve Mimari İnceleme
1. **Veritabanı Şeması İncelemesi:**
   `candles` tablosunun birincil anahtarı `(instrument_id, source_id, timeframe, open_time, price_type, revision)` şeklindedir.
   Veritabanında `source_id`'nin tek bir enstrümana ait olmasını zorunlu tutan bir FOREIGN KEY veya UNIQUE kısıtı bulunmamaktadır.
2. **DISTINCT ON İncelemesi:**
   Eski `get_candles` sorgusunda:
   ```sql
   SELECT DISTINCT ON (open_time, price_type) ...
   FROM candles WHERE source_id = :source_id ...
   ORDER BY open_time, price_type, revision DESC;
   ```
   Eğer aynı sağlayıcı (`source_id = 'dukascopy'`) adı altında hem XAUUSD hem de EURUSD mumları kaydedilmiş olsaydı, `DISTINCT ON` enstrümanı dikkate almadığı için aynı zaman damgasındaki iki enstrümandan biri diğerini ezip yok edecek, veya XAUUSD sorgusu EURUSD mumlarını döndürecekti.

### B. Uygulanan Düzeltmeler (En Küçük Güvenilir Değişiklik)
1. **DISTINCT ON Sorgusuna `instrument_id` Eklenmesi:**
   `distinct_on(CandleModel.instrument_id, CandleModel.open_time, CandleModel.price_type)` ve `order_by(CandleModel.instrument_id, CandleModel.open_time, CandleModel.price_type, CandleModel.revision.desc())` tanımlanmıştır. Hiçbir koşulda bir enstrümanın mumu diğerini ezemez.
2. **Repository Portuna `instrument_id` Filtresi Eklenmesi:**
   [`CandleRepository.get_candles`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/ports.py#L63-L75) portuna geriye uyumlu `instrument_id: InstrumentId | None = None` parametresi eklenmiş; belirtildiğinde SQL filtresine `CandleModel.instrument_id == str(instrument_id)` eklenmiştir.
3. **Repository Seviyesinde DataSource-Instrument Birebir Eşleme Denetimi:**
   `save_candles` fonksiyonunda:
   - Hem gelen batch içinde tek bir `source_id`'nin birden fazla enstrümana atanması engellenmiştir (`ValueError`).
   - Hem de veritabanında daha önce kaydedilmiş bir `source_id`'nin başka bir enstrüman tarafından gasp edilmesi engellenmiştir (`ValueError`).

İlgili Test:
- [`TestPostgresCandleRepository::test_instrument_and_source_isolation_and_distinct_on`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L747-L876) (**GEÇTİ**)

---

## 5. KRİTİK KONTROL C — BITEMPORAL PIT VE SNAPSHOT DOĞRULUĞU

Gerçek PostgreSQL üzerinde 7 senaryo eksiksiz test edilmiştir:
1. **Senaryo 1 & 2:** Geçmiş bir mumun ilk versiyonu (v0, `close=2502.00`) ve ardından düzeltilmiş versiyonu (v1, `close=2508.00`) kaydedilmiştir.
2. **Senaryo 3:** Eski snapshot (`t0`) ile yapılan sorguda eski versiyon (`revision=0`, `close=2502.00`) dönmüştür.
3. **Senaryo 4:** Yeni snapshot (`t1`) ile yapılan sorguda güncel düzeltilmiş versiyon (`revision=1`, `close=2508.00`) dönmüştür.
4. **Senaryo 5:** `STRICT_PIT`, `availability_confidence=ESTIMATED` olan mumu reddetmiştir (sonuç: boş liste).
5. **Senaryo 6:** `RESEARCH_ESTIMATED`, geçmişten `available_at=None` ile indirilmiş tarihsel backfill mumunu başarıyla kabul etmiştir.
6. **Senaryo 7:** Karar anında henüz tamamlanmamış mum (`close_time > as_of_utc`) her iki modda da reddedilmiştir.

### `ingested_at` ve Transaction Commit Görünürlük Analizi (Kritik Teknik Bulgu)
PostgreSQL'de `ingested_at` alanı `clock_timestamp()` ile doldurulmaktadır.
- `clock_timestamp()` fonksiyonu SQL ifadesinin **çalıştırıldığı anın** duvar saati zamanını kaydeder.
- Ancak PostgreSQL MVCC modelinde, yazan transaction **commit edilene kadar** bu satır eşzamanlı okuyucular tarafından görülemez.
- Yürütme ile commit arasında geçen süre (ağ gecikmesi, disk fsync vb. nedeniyle tipik olarak 5–50 ms) boyunca; o aralıktaki bir `knowledge_snapshot_time` sorgusu transaction henüz commit edilmediği için satırı göremezken, transaction commit edildikten sonra aynı `knowledge_snapshot_time` ile yapılacak yeni bir sorgu satırı görür.
- **Raporlanan Gerçeklik:** Canlı akış sırasında in-flight (henüz commit edilmemiş) yazmalar devam ederken mikro-saniyelik snapshot kesinliği garanti edilemez. Yalnızca commit edilmiş zaman damgaları kesin PIT garantisi sunar. Tarihsel backtestlerde snapshot zamanı her zaman yazma pencerelerinden daha muhafazakâr bir referans noktasına kurulmalıdır.

İlgili Test:
- [`TestPostgresCandleRepository::test_bitemporal_pit_scenarios_comprehensive`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L878-L1038) (**GEÇTİ**)

---

## 6. KRİTİK KONTROL D — SQL SORGUSU VE PERFORMANS DEĞERLENDİRMESİ

Gerçek PostgreSQL üzerinde sentetik 500 mumluk test verisiyle `EXPLAIN (ANALYZE, BUFFERS, VERBOSE)` çalıştırılmıştır.

### PostgreSQL Sorgu Planı Çıktısı

```text
Unique  (cost=52.80..54.08 rows=171 width=156) (actual time=0.212..0.250 rows=336 loops=1)
  Output: instrument_id, source_id, timeframe, open_time, price_type, revision, ...
  Buffers: shared hit=23
  ->  Sort  (cost=52.80..53.23 rows=171 width=156) (actual time=0.211..0.220 rows=336 loops=1)
        Output: instrument_id, source_id, timeframe, open_time, price_type, revision, ...
        Sort Key: candles.instrument_id, candles.open_time, candles.revision DESC
        Sort Method: quicksort  Memory: 114kB
        Buffers: shared hit=23
        ->  Bitmap Heap Scan on public.candles  (cost=28.69..46.46 rows=171 width=156) (actual time=0.076..0.135 rows=336 loops=1)
              Output: instrument_id, source_id, timeframe, open_time, price_type, revision, ...
              Recheck Cond: (candles.source_id = 'feed-bench' AND candles.timeframe = 3600 AND candles.price_type = 'BID' AND candles.open_time >= '2024-01-01 00:00:00+00' AND candles.open_time < '2024-01-15 00:00:00+00' AND candles.ingested_at <= '2026-10-21 00:00:00+00' AND candles.available_at IS NOT NULL AND candles.available_at <= '2024-01-16 00:00:00+00')
              Filter: (candles.is_closed AND candles.close_time <= '2024-01-16 00:00:00+00' AND candles.availability_confidence = 'VERIFIED')
              Heap Blocks: exact=7
              Buffers: shared hit=11
              ->  Bitmap Index Scan on idx_candles_pit_lookup  (cost=0.00..28.65 rows=239 width=0) (actual time=0.069..0.069 rows=336 loops=1)
                    Index Cond: (candles.source_id = 'feed-bench' AND candles.timeframe = 3600 AND candles.price_type = 'BID' AND candles.open_time >= '2024-01-01 00:00:00+00' AND candles.open_time < '2024-01-15 00:00:00+00' AND candles.ingested_at <= '2026-10-21 00:00:00+00' AND candles.available_at IS NOT NULL AND candles.available_at <= '2024-01-16 00:00:00+00')
                    Buffers: shared hit=4
Planning:
  Buffers: shared hit=194
Planning Time: 1.443 ms
Execution Time: 0.300 ms
```

### Performans Analizi ve Sınırları
1. **İndeks Kullanımı:**  
   `idx_candles_pit_lookup` kompozit indeksi (`source_id`, `timeframe`, `price_type`, `open_time`, `ingested_at`, `available_at`) arama koşullarını doğrudan indeks üzerinden karşılamakta ve sadece 4 paylaşılan tampon (`shared hit=4`) ile 336 mumu filtrelemektedir.
2. **Yürütme Hızı:**  
   İndeks araması 0.069 ms, bellek içi quicksort 0.009 ms, toplam sorgu süresi **0.300 ms** olarak gerçekleşmiştir.
3. **Milyonlarca Mum İçin Sınırlılıklar (Kanıtsız $O(\log N)$ İddiasının Reddi):**  
   - Küçük sentetik veri setindeki 0.300 ms'lik performans, tablonun RAM (buffer cache) içinde kalmasından kaynaklanmaktadır.
   - 10+ yıllık M1 verisi (~4 milyon satır) depolandığında, indeks boyutu RAM'i aşabilir ve `Bitmap Heap Scan` rastgele disk G/Ç (random I/O) maliyetine dönüşebilir.
   - Bu nedenle Faz 1B'de veriler büyüdükçe indeks boyutu ve cache hit ratio metrikleri yakından izlenmelidir. Ancak mevcut faz sınırları gereği partitioning veya TimescaleDB gibi teknolojilere bu aşamada geçilmemiştir.

---

## 7. DEĞİŞTİRİLEN VE EKLENEN DOSYALAR

| Dosya | Değişiklik Türü | Açıklama |
| :--- | :--- | :--- |
| [`src/traderlab/domain/ports.py`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/ports.py) | Değiştirildi | `CandleRepository.get_candles` metoduna `instrument_id` eklendi |
| [`tests/test_domain_ports.py`](file:///c:/trader/traderLab/traderlab/tests/test_domain_ports.py) | Değiştirildi | `FakeCandleRepository.get_candles` metoduna `instrument_id` desteği eklendi |
| [`src/traderlab/infrastructure/database/repositories/candle_repository.py`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/repositories/candle_repository.py) | Değiştirildi | Transactional advisory lock, sıralı kilitleme, deduplication, enstrüman izolasyonu ve DISTINCT ON düzeltmesi |
| [`tests/integration/test_postgres_candle_repository.py`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py) | Değiştirildi | 5 yeni kritik regresyon testi eklendi |
| [`docs/reports/phase-1a-k1.md`](file:///c:/trader/traderLab/traderlab/docs/reports/phase-1a-k1.md) | Yeni | Faz 1A-K1 kapsamlı teknik denetim raporu |

---

## 8. EKLENEN REGRESYON TESTLERİ

| Test Adı | Doğrulanan Mimari Davranış | Sonuç |
| :--- | :--- | :---: |
| `test_concurrent_revisions_no_silent_loss` | İki bağımsız async oturumun eşzamanlı farklı fiyat revizyonu yazması; hiçbir düzeltmenin kaybolmaması, rev 1 ve rev 2 olarak serileşmesi | **GEÇTİ** |
| `test_repeated_identical_data_does_not_create_redundant_revision` | Birebir aynı OHLCV verisine sahip mumun tekrar kaydedilmesinde revizyon numarasının artırılmaması (idempotent no-op) | **GEÇTİ** |
| `test_instrument_and_source_isolation_and_distinct_on` | Bir `source_id`'nin birden fazla enstrümana atanmasının engellenmesi, `instrument_id` filtresi ve DISTINCT ON izolasyonu | **GEÇTİ** |
| `test_bitemporal_pit_scenarios_comprehensive` | Senaryo 1-7 (v0/v1 sürümleme, eski/yeni snapshot, STRICT_PIT doğrulaması, RESEARCH_ESTIMATED backfill, kapanmamış bar reddi) | **GEÇTİ** |
| `test_concurrent_batch_deadlock_prevention` | Ters sırada (`[A, B]` vs `[B, A]`) yazılan toplu mum işlemlerinde deterministik sıralama ile deadlock oluşmaması | **GEÇTİ** |

---

## 9. OTOMATİK KALİTE TESTLERİ GERÇEK SONUÇLARI

### Pytest
```text
165 passed in 1.29s (147 unit/contract + 18 PostgreSQL integration)
165 tests collected in 0.51s
```

### Ruff Check & Format
```text
All checks passed!
57 files already formatted
```

### Mypy Type Checking
```text
Success: no issues found in 42 source files
```

---

## 10. AÇIK KALAN TEKNİK RİSKLER

1. **İstemci-Sunucu Saat Kayması (Clock Skew):**  
   Uygulama sunucusu (veya test ortamı) saati ile PostgreSQL `clock_timestamp()` arasında milisaniyelik kaymalar (~50 ms) olabilmektedir. Canlı ingestion hattında snapshot filtrelerinin bu gecikmeyi tolere edecek muhafazakâr aralıklarla yapılandırılması gerekmektedir.
2. **Hash Çakışması (Hash Collision in Advisory Locks):**  
   `hashtext(lock_key)` 32-bit tamsayı üretmektedir. Milyarlarca farklı mum anahtarı için matematiksel olarak hash çakışması olasılığı teorik olarak vardır; ancak bir çakışma durumunda tek etki bir oturumun diğerini milisaniyelerce beklemesidir (veri bozulması veya sessiz kayıp kesinlikle gerçekleşmez).
3. **Çok Büyük Tablolarda Heap Fetch Yükü:**  
   Tablo boyutu yüz binlerce muma ulaştığında `idx_candles_pit_lookup` indeksinden sonra tablo sayfalarına erişim (`Bitmap Heap Scan`) maliyet oluşturabilir. İlerleyen fazlarda `INCLUDE` indeksleri veya partition yapıları değerlendirilmelidir.

---

## 11. FAZ 1B'YE GEÇİŞ İÇİN TAVSİYE VE NİHAİ KARAR

- **Faz 1A-K1 Kapsamı:** Tamamlandı.
- **Veri Bütünlüğü Güvencesi:** Eşzamanlı revizyon yarış koşulları, gereksiz revizyon artışları, enstrüman sızıntıları ve bitemporal snapshot tutarsızlıkları gerçek PostgreSQL üzerinde çözülmüş ve testlerle kanıtlanmıştır.
- **Nihai Karar:** **`READY`**
- **Sonraki Adım Tavsiyesi:** Kullanıcı onayının ardından Faz 1B'ye (gerçek broker/veri sağlayıcı adaptörleri ve kontrollü veri toplama) geçilebilir. Bu onay verilene kadar hiçbir harici veri indirilmeyecek ve yeni faza başlanmayacaktır.
