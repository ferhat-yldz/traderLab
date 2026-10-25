# TRADERLAB — FAZ 1A-K2 TEKNİK DENETİM VE KABUL RAPORU
## Son Veri Bütünlüğü, Cross-Instrument Eşzamanlılık ve Metadata Idempotency Kontrolü

**Tarih:** 2026-10-20  
**Mühendislik Rolü:** Senior PostgreSQL Database Engineer & Quantitative Software Engineer  
**Kapsam:** Cross-Instrument Concurrent Source Binding, Metadata-Aware Idempotency Contract, Commit-Time PIT Semantiği ve Görünürlük Analizi  
**Nihai Karar:** **`READY`**

---

## 1. YÖNETİCİ ÖZETİ VE BAŞLANGIÇ/BİTİŞ TEST SAYILARI

Faz 1A-K1 sonrasında tespit edilen iki kritik eşzamanlılık/idempotency açığı ve PIT commit zamanı sınırlandırması doğrudan gerçek PostgreSQL 14 (`traderlab_test`) üzerinde araştırılmış, çözülmüş ve yeni regresyon testleriyle güvence altına alınmıştır.

| Metrik | Faz 1A-K1 Sonu (Başlangıç) | Faz 1A-K2 Sonu (Bitiş) | Değişim | Durum |
| :--- | :---: | :---: | :---: | :---: |
| **Toplam Pytest Test Sayısı** | **165** | **168** | **+3 yeni regresyon testi** | **%100 GEÇTİ** |
| **PostgreSQL Entegrasyon Testleri** | 18 | 21 | +3 yeni entegrasyon testi | %100 GEÇTİ |
| **Birim / Kontrat / Smoke Testleri** | 147 | 147 | Eksiksiz Korundu | %100 GEÇTİ |
| **Test Yürütme Süresi** | ~1.29s | 1.53s | — | Ultra Hızlı |
| **Ruff Check & Format** | Geçti | Geçti | 57 dosya temiz | Hata Yok |
| **Mypy Type Checking** | Geçti | Geçti | 42 kaynak dosya temiz | Hata Yok |

---

## 2. GÖREV 1 — CROSS-INSTRUMENT CONCURRENT SOURCE TESTİ VE ATOMİK KORUMA

### A. Tespit Edilen Problem (Cross-Instrument Concurrent Source Race)
Faz 1A-K1'de mum revizyon kilitleri `lock_key = f"{candle.instrument_id}:{candle.source_id}:..."` şeklinde tanımlanmıştı.
- **Açık Senaryo:** Veritabanında henüz kayıtlı olmayan yeni bir `source_id = "feed-shared"` için iki bağımsız async oturum aynı anda farklı enstrümanlar kaydetmek istediğinde (Worker 1: `XAUUSD`, Worker 2: `EURUSD`):
  - Kilit anahtarı `instrument_id` içerdiği için Worker 1 `XAUUSD:feed-shared` kilidini, Worker 2 `EURUSD:feed-shared` kilidini alıyordu.
  - İki oturum birbirini bloke etmiyordu!
  - İkisi de veritabanında `source_id == "feed-shared"` aradığında `None` görüyordu.
  - İki oturum da yazma işlemini başarıyla tamamlıyor ve tek bir `source_id` veritabanında aynı anda hem XAUUSD hem EURUSD'ye bağlanarak kimlik kirlenmesine (identity corruption) yol açıyordu.

### B. Uygulanan Çözüm
1. **`source_id` Düzeyinde Transactional Advisory Lock:**  
   [`PostgresCandleRepository.save_candles`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/repositories/candle_repository.py#L130-L138) fonksiyonunda, batch içinde yer alan benzersiz veri kaynakları deterministik alfabetik sırayla sıralanarak her biri için işlem düzeyinde kilit alındı:
   ```python
   for sid in unique_sources:
       source_lock_key = f"source_binding:{sid}"
       await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(source_lock_key))))
   ```
2. **Atomik Eşleme Garantisi:**  
   Worker 1 ve Worker 2 aynı `source_id`'yi ilk kez kaydederken `source_binding:feed-shared` kilidinde serileşir. Worker 1 satırı yazıp commit edene kadar Worker 2 bekletilir. Worker 1 commit ettikten sonra Worker 2 uyanır, veritabanında `source_id`'nin artık XAUUSD'ye ait olduğunu görür ve anında `ValueError("DataSourceId '...' is already bound to instrument 'XAUUSD'")` fırlatarak işlemi iptal eder.
3. **`provider_id` ile `source_id` Ayrımı:**  
   Bu kilit yalnızca `DataSourceId` (enstrümana özel feed kimliği, örn. `dukascopy_xauusd` veya `feed-alpha`) için geçerlidir. Bir veri sağlayıcısının (`ProviderId`, örn. `dukascopy`) farklı enstrümanlara (`dukascopy_xauusd` ve `dukascopy_eurusd`) bağımsız veri sunması hiçbir şekilde engellenmez.

### C. Doğrulayan Test
- [`TestPostgresCandleRepository::test_cross_instrument_concurrent_source_atomic_isolation`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L1110-L1180): İki oturumun aynı anda aynı `source_id` ile XAUUSD ve EURUSD kaydetme yarışı çalıştırılmış; tam olarak bir işçinin başarılı olduğu, diğerinin `ValueError` ile reddedildiği ve veritabanında yalnızca tek bir enstrümanın kaldığı kanıtlanmıştır.

---

## 3. GÖREV 2 — METADATA-AWARE IDEMPOTENCY VE SÖZLEŞME

### A. Tespit Edilen Problem
Eski idempotency kontrolü yalnızca OHLC, hacim ve spread alanlarını kıyaslıyordu. Fiyat değişmediği hâlde metadata güncellemeleri (örneğin açık mumun kapanması, veri güvenilirliğinin `ESTIMATED`'dan `VERIFIED`'a yükseltilmesi, `available_at` atanması veya sentetik barın gerçek veriye dönüşmesi) yapıldığında sistem verinin aynı olduğunu varsayıp güncellemeyi sessizce yutuyordu (silent update loss).

### B. Açık Domain Veri Değişikliği Sözleşmesi
[`_is_candle_data_identical`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/repositories/candle_repository.py#L34-L78) fonksiyonunda 11 adet domain alanı açık bir sözleşmeyle tanımlandı:

| Kategori | Alan Adı | Veri Değişikliği ve Revizyon Bumping Kuralı |
| :--- | :--- | :--- |
| **Fiyat (OHLC)** | `open`, `high`, `low`, `close` | Herhangi bir fiyatta en ufak Decimal farkı yeni revizyon gerektirir. |
| **Hacim** | `volume`, `tick_volume` | Gerçek hacim veya broker tick frekansı değiştiğinde revizyon artırılır. |
| **Mikro-yapı** | `spread` | Broker spread farkı revizyon artırır. |
| **Bar Durumu** | `is_closed` | **Kritik:** Açık mumun (`is_closed=False`) kapanmış muma (`is_closed=True`) dönüşmesi ASLA kaybolmaz, yeni revizyonla (`rev=1`) kalıcılaşır. |
| **Sentetiklik** | `is_synthetic` | Sentetik barın gerçek piyasa verisiyle güncellenmesi yeni revizyon üretir. |
| **PIT & Güvenilirlik** | `availability_confidence` | `ESTIMATED` durumunun `VERIFIED` olarak güncellenmesi yeni revizyon üretir. |
| **Erişilebilirlik Zamanı** | `available_at` | `None` olan erişilebilirlik zamanının teyit edilmesi revizyon üretir. |
| **Sağlayıcı Lineage** | `published_at`, `received_at` | Ağ yayın ve alış zamanı güncellemeleri revizyon üretir. |
| **Teknik Alan (Hariç)** | `ingested_at` | **İstisna:** Veritabanına fiziksel yazılma zamanı teknik bir alandır; domain verisi değişmedikçe tek başına gereksiz revizyon artışı YARATMAZ (idempotent duplicate). |

### C. Doğrulayan Test
- [`TestPostgresCandleRepository::test_metadata_aware_idempotency_transitions`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L1182-L1374): OHLC sabitken 6 ardışık metadata geçişi (`is_closed: False->True`, `confidence: ESTIMATED->VERIFIED`, `available_at`, `tick_volume`, `spread`, `is_synthetic`) sırasıyla test edilmiş; her birinin `rev=0`'dan `rev=6`'ya kadar revizyon artırdığı ve ardından gelen teknik `ingested_at` farklı aynı muma 0 satır dönüldüğü doğrulanmıştır.

---

## 4. GÖREV 3 — PIT SEMANTİK KONTROLÜ VE COMMIT GÖRÜNÜRLÜK ANALİZİ

### A. Empirik Kanıt (`clock_timestamp()` vs Commit Time)
[`TestPostgresCandleRepository::test_in_flight_commit_visibility_lag`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py#L1376-L1442) testi ile PostgreSQL üzerinde doğrudan kanıtlanmıştır:
1. Oturum 1 bir mumu veritabanına yazar ve flush eder (`ingested_at = clock_timestamp()` = `T_insert`). Ancak transaction commit edilmez (in-flight).
2. Oturum 2, `knowledge_snapshot_time = T_insert + 50ms` parametresiyle sorgu çalıştırır. PostgreSQL MVCC kuralı gereği Oturum 1 henüz commit etmediği için **0 satır** döner.
3. Oturum 1 transaction'ı `T_commit = T_insert + 100ms` anında commit eder.
4. Oturum 3, BİREBİR AYNI `knowledge_snapshot_time = T_insert + 50ms` parametresiyle sorgu çalıştırır. Satır artık commit edildiği ve `ingested_at (T_insert) <= T_snap` olduğu için **1 satır** döner!

### B. Belgelenen Mimari Sınırlama (Açık ve Dürüst Rapor)
> [!IMPORTANT]
> **Commit Görünürlük Sınırlaması (Commit Visibility Lag):**
> PostgreSQL'de `ingested_at = clock_timestamp()` değeri transaction'ın **commit edildiği zamanı değil, SQL ifadesinin çalıştırıldığı anı** temsil eder.
> Bu nedenle:
> - Canlı veri akışında in-flight yazma işlemleri sürerken, mikrosaniyelik anlık snapshot sorguları henüz commit edilmemiş satırları göremez.
> - Ancak aynı satırlar commit edildikten sonra yapılacak geçmişe dönük (retrospective) bir sorgu, satırın `ingested_at` zamanı geride kaldığı için satırı görecektir.
> - **Sonuç ve Kural:** TraderLab'de kesin, yüzde yüz tekrarlanabilir PIT garantisi, yalnızca yazma işlemleri tamamlanmış (commit edilmiş) zaman pencereleri için geçerlidir. Canlı backtestlerde snapshot zamanı her zaman yazma pencerelerinden daha muhafazakâr bir referans noktasına kurulmalıdır. Bu sınırlama PostgreSQL çekirdeğinin MVCC doğasından kaynaklanmakta olup sistemde sahte bir commit-time garantisi varmış gibi sunulmamıştır.

---

## 5. DEĞİŞTİRİLEN VE EKLENEN DOSYALAR

| Dosya | Değişiklik Türü | Açıklama |
| :--- | :--- | :--- |
| [`src/traderlab/infrastructure/database/repositories/candle_repository.py`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/repositories/candle_repository.py) | Değiştirildi | `source_binding:{sid}` advisory lock eklendi; `_is_candle_data_identical` fonksiyonu 11 domain alanını kapsayacak şekilde genişletildi |
| [`tests/integration/test_postgres_candle_repository.py`](file:///c:/trader/traderLab/traderlab/tests/integration/test_postgres_candle_repository.py) | Değiştirildi | 3 yeni entegrasyon testi eklendi (`test_cross_instrument_concurrent_source_atomic_isolation`, `test_metadata_aware_idempotency_transitions`, `test_in_flight_commit_visibility_lag`) |
| [`docs/reports/phase-1a-k2.md`](file:///c:/trader/traderLab/traderlab/docs/reports/phase-1a-k2.md) | Yeni | Faz 1A-K2 teknik kabul raporu |

---

## 6. OTOMATİK KALİTE TESTLERİ GERÇEK SONUÇLARI

### Pytest
```text
============================= 168 passed in 1.53s =============================
168 tests collected in 0.48s
(147 unit/contract + 21 PostgreSQL integration)
```

### Ruff Linter & Formatter
```text
All checks passed!
57 files already formatted
```

### Mypy Type Checking
```text
Success: no issues found in 42 source files
```

---

## 7. AÇIK KALAN TEKNİK RİSKLER

1. **Commit Timestamp Takibi:**  
   İlerleyen aşamalarda PostgreSQL düzeyinde gerçek commit anını mikrosaniyelik kaydetmek istenirse PostgreSQL'in `track_commit_timestamp = on` özelliği veya logical replication commit LSN mekanizmaları değerlendirilebilir. Mevcut Faz 1A kapsamında `clock_timestamp()` muhafazakâr snapshot aralıklarıyla araştırma ve bitemporal ihtiyaçları eksiksiz karşılamaktadır.
2. **Kilit Çakışması (Hash Space):**  
   Advisory lock anahtarları 32-bit `hashtext` kullandığından trilyonlarca anahtarda çakışma teorik olarak mümkündür. Çakışma durumunda tek etki milisaniyelik beklemedir (veri bozulması veya sessiz kayıp imkansızdır).

---

## 8. NİHAİ KABUL KARARI

- **Faz 1A-K2 Görev Durumu:** Görev 1, Görev 2 ve Görev 3 tam başarıyla tamamlanmıştır.
- **Güvenlik Sınırları:** Eski `borsa/` ve `trader_muhendis` sistemlerine dokunulmamış; broker API veya canlı veri indirilmemiştir.
- **Nihai Karar:** **`READY`**
- **Tavsiye:** Faz 1A altyapısı tüm eşzamanlılık, veri bütünlüğü, bitemporal PIT ve idempotency testlerinden tam not almıştır. Kullanıcı onayının ardından Faz 1B'ye geçilebilir.
