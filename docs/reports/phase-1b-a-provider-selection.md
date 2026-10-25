# TRADERLAB — FAZ 1B-A TEKNİK ARAŞTIRMA VE KARAR RAPORU
## XAUUSD Tarihsel Veri Sağlayıcısı Araştırması ve Teknik Seçim

**Tarih:** 2026-10-20  
**Mühendislik Rolü:** Senior Financial Data Engineer, Quantitative Trading Researcher & Market Data Infrastructure Architect  
**Kapsam:** XAUUSD (Spot Altın) Tarihsel Piyasa Verisi Sağlayıcılarının Kapsamlı Karşılaştırmalı Analizi ve Mimari Seçimi  
**Nihai Karar:** **1. Tercih: Dukascopy Historical Data / 2. Tercih: OANDA REST API (v20)**  
**Durum:** **`RESEARCH_COMPLETE_AWAITING_APPROVAL`**

---

## 1. YÖNETİCİ ÖZETİ VE HEDEF

TRADERLAB araştırma platformunda Faz 1A-K2 başarıyla tamamlanmış; PostgreSQL veri saklama omurgası, transactional advisory locking, metadata-aware idempotency ve bitemporal PIT sorgulama yetenekleri gerçek veritabanı üzerinde 168 otomatik test ile %100 oranında doğrulanmıştır.

Bu fazın (Faz 1B-A) amacı, **XAUUSD (Spot Gold vs US Dollar)** enstrümanı için:
- Güvenilir, kurumsal kalitede ve doğrulanabilir,
- Mümkünse sıfır/düşük maliyetli,
- ICT/SMC (Fair Value Gap, Liquidity Sweeps, Order Block, BOS/MSS), Price Action ve çoklu zaman dilimi (Multi-Timeframe) analizlerini destekleyen,
- Bağımsız BID ve ASK fiyatlarını, hassas spread ve tick hacmini eksiksiz içeren,
- TraderLab Clean/Hexagonal Architecture domain ve infrastructure portlarıyla doğrudan uyumlu

en uygun tarihsel veri sağlayıcısını bilimsel ve teknik kriterlerle seçmektir.

> [!IMPORTANT]
> **Kesin Çalışma Sınırları Teyidi:**  
> Bu aşama yalnızca araştırma ve teknik değerlendirme fazıdır. Bu raporda hiçbir harici kaynaktan gerçek veri indirilmemiş, broker API hesabı açılmamış, PostgreSQL şemasına müdahale edilmemiş ve mevcut kaynak kodlarında hiçbir değişiklik yapılmamıştır.

---

## 2. VERİ SAĞLAYICILARI KARŞILAŞTIRMA MATRİSİ

15 temel finansal ve teknik kriter üzerinden incelenen 4 ana alternatif:

| Kriter # | Kriter Tanımı | Dukascopy Historical Data | OANDA REST API (v20) | MetaTrader 5 (MT5 Broker) | HistData.com (Arşiv) |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **1** | **XAUUSD Desteği** | **Tam Destek** (`XAU/USD`, spot) | **Tam Destek** (`XAU_USD`) | **Broker Bağımlı** (`XAUUSD`, `GOLD`) | **Destekleniyor** |
| **2** | **Tarihsel Kapsam** | **2003–Günümüz** (15+ yıl kesintisiz) | **2005–Günümüz** (~20 yıl) | Genellikle 1–3 yıl M1 (Sunucuya bağlı) | 2010–2024 (Eski arşiv) |
| **3** | **Zaman Dilimleri** | **Tick, M1, M5, M15, H1, D1** | M1, M5, M15, H1, D1 (S5–M) | Tick, M1, M5, M15, H1, D1 | Yalnızca Tick ve M1 |
| **4** | **Gerçek BID / ASK** | **Ayrı Ayrı Mevcut** (Tick & Bar) | **Ayrı Ayrı Mevcut** (`price=BA`) | Bar Bid tabanlı; Ask tick'ten türetilir | Tick BID/ASK; M1 çoğunlukla BID |
| **5** | **Spread & Hassasiyet** | 3 ondalık ($0.001), gerçek spread | 2–3 ondalık, dinamik spread | Broker spread modeli (raw/markup) | Değişken / Sentetik |
| **6** | **Zaman Damgası & UTC** | **Kesin UTC** (Zaman kayması yok) | **RFC3339 UTC** | **Broker EET (UTC+2/UTC+3)** | **EST (ABD Doğu)** DST kaymalı |
| **7** | **Feed / Broker Kimliği** | **İsviçre ECN Bankası** (Dukascopy) | OANDA Market Maker / STP | Seçilen MT5 Brokerı (örn. IC Markets) | Belirsiz broker agregasyonu |
| **8** | **Hafta Sonu & Seanslar** | Standart Forex seansları (Pazar-Cuma) | Standart seanslar, temiz tatil filtresi | Broker seans takvimine bağımlı | Eksik barlar / boşluklar mevcut |
| **9** | **Erişim Yöntemi** | HTTP GET (Açık `.bi5` LZMA Arşivi) | REST API (JSON endpoint) | Python C-API (MT5 Terminal IPC) | Manuel Web Zip İndirme / Scraper |
| **10** | **Maliyet** | **%100 ÜCRETSİZ** | **Ücretsiz Demo Hesabı** | Broker Demo Hesabı ile Ücretsiz | Ücretsiz (Manuel) / Ücretli API |
| **11** | **Kayıt / API Key** | **GEREK YOK** (Açık Erişim) | API Key Zorunlu (Demo/Canlı) | Broker Giriş Bilgileri Zorunlu | Manuel için gerek yok |
| **12** | **Kullanım Lisansı** | Kişisel & Araştırma Serbest | Geliştirici Sözleşmesi | Broker Sözleşmesi | Ticari Olmayan Kullanım |
| **13** | **Python 3.11 Uyumu** | **Kusursuz** (`lzma`, `struct`, `httpx`) | **Kusursuz** (`httpx`, `aiohttp`) | **Yalnızca Windows + MT5 Terminal** | Manuel Pandas CSV okuma |
| **14** | **Büyük Veri İndirme** | Çok Hızlı (Saatlik sıkıştırılmış bi5) | Sayfalama Zorunlu (5.000 bar/istek) | Hızlı fakat yerel terminal açık olmalı | Büyük zip dosyaları, yavaş |
| **15** | **Tekrar Üretilebilirlik** | **Mükemmel** (Değişmez ham veri) | **Yüksek** (API sürümlü) | **Düşük** (Broker geçmişi değiştirebilir) | **Orta** (Zaman dilimi dönüşüm riski) |

---

## 3. DERİNLEMESİNE TEKNİK İNCELEMELER

### A. DUKASCOPY HISTORICAL DATA (1. ÖNERİ — TAVSİYE EDİLEN)
Dukascopy Bank SA, İsviçre FINMA regülasyonuna tabi bir ECN broker ve bankadır. Bireysel ve kurumsal likidite havuzunun tick düzeyindeki tüm işlem akışını `.bi5` formatında halka açık HTTP sunucularında barındırır.

#### 1. Veri Yapısı ve Kod Çözme Mantığı (.bi5 Binary Yapısı)
Dukascopy tick verileri, saatlik LZMA ile sıkıştırılmış 20 baytlık sabit ikili bloklardan oluşur:
- **URL Formatı:** `https://datafeed.dukascopy.com/datafeed/{SYMBOL}/{YEAR}/{MONTH_0_INDEXED}/{DAY}/{HOUR}h_ticks.bi5`
- **İkili Yapı (Big-Endian `>IIIff`):**
  - Bayt 0–3 (`uint32`): Saat başlangıcından itibaren geçen milisaniye (`time_offset_ms`).
  - Bayt 4–7 (`uint32`): Ham Ask fiyatı (Raw Integer).
  - Bayt 8–11 (`uint32`): Ham Bid fiyatı (Raw Integer).
  - Bayt 12–15 (`float32`): Ask işlem hacmi (milyon baz birim).
  - Bayt 16–19 (`float32`): Bid işlem hacmi (milyon baz birim).
- **XAUUSD Fiyat Çarpanı:** Dukascopy'de XAU/USD için bölen **`1000.0`**'dir.  
  $$\text{Real Price} = \frac{\text{Raw Integer}}{1000.0}$$
  *Örnek:* Ham değer `2650450` $\rightarrow$ Gerçek Fiyat: **`2650.450 USD`**.

#### 2. Güçlü Yönleri
- **Kayıt ve Yetkilendirme Yok:** API anahtarı, OAuth veya aylık yenilenmesi gereken demo hesabı gerektirmez.
- **Gerçek BID ve ASK:** Hem alış hem satış fiyatları eşzamanlı olarak milisaniyelik UTC zaman damgasıyla mevcuttur.
- **Hafif ve Hızlı:** Standart Python `lzma` ve `struct` modülleri ile saniyede on binlerce tick C hızında ayrıştırılır; hiçbir harici ağır kütüphaneye ihtiyaç duymaz.
- **Platform Bağımsızlığı:** Hem Windows geliştirme ortamında hem de Linux Docker konteynerlerinde doğrudan çalışır.

#### 3. Sınırlandırmalar ve Riskler
- **Hız Sınırı (Rate Limiting):** Aşırı agresif paralel isteklerde geçici IP blokajı (`HTTP 429/403`) uygulanabilir. İstekler arasında 50–100 ms nezaket beklemesi ve `keep-alive` bağlantı havuzu kullanılmalıdır.
- **Veri Türetme Sorumluluğu:** M1 veya H1 mumları doğrudan indirilebildiği gibi, ham tick verisinden türetilecekse mum oluşturma mantığı TraderLab içinde deterministik olarak yazılmalıdır.

---

### B. OANDA REST API v20 (2. ÖNERİ — YEDEK & DOĞRULAMA KAYNAĞI)
OANDA, küresel çapta tanınan bir perakende ve kurumsal Forex/CFD piyasa yapıcıdır (Market Maker/STP). v20 REST API üzerinden yüksek kaliteli geçmiş mum verisi sunar.

#### 1. API Özellikleri ve Parametreleri
- **Endpoint:** `GET /v3/instruments/{instrument}/candles`
- **XAUUSD Sembolü:** `XAU_USD`
- **Parametreler:**
  - `price=BA`: Hem BID hem de ASK barlarını tek yanıtta döner.
  - `granularity`: `M1`, `M5`, `H1`, `D` vb.
  - `count`: Maksimum **5.000 mum/istek**.
- **Oran Sınırları (Rate Limits):** IP başına saniyede maksimum **120 istek** ve saniyede en fazla **2 yeni TCP bağlantısı**.

#### 2. Güçlü Yönleri
- **Hazır Agregasyon:** M1, M5, H1 barları doğrudan JSON formatında döner; istemci tarafında tick agregasyonuna gerek kalmaz.
- **REST Standartları:** RFC3339 UTC zaman damgaları ve JSON formatı ile entegrasyonu son derece kolaydır.

#### 3. Sınırlandırmalar ve Riskler
- **Hesap ve Token Bağımlılığı:** Bir OANDA Practice hesabı ve API Token gerektirir. Demo hesaplar 30 gün işlem yapılmadığında inaktif olabilir.
- **5.000 Bar Sınırı:** 10 yıllık M1 verisi (~3.5 milyon mum) indirmek için **700+ ardışık sayfalama isteği** yapılması gerekir.
- **Ham Tick Kısıtı:** Uzun geçmişe dönük ham tick verisi REST üzerinden tam olarak sunulmaz; ağırlıklı olarak mum verisi sağlar.

---

### C. METATRADER 5 (MT5 BROKER ENTEGRASYONU)
MetaTrader 5 Python kütüphanesi (`MetaTrader5`), yerel makinede çalışan MT5 terminaline IPC (Inter-Process Communication) ile bağlanarak broker sunucusundaki veriyi çeker.

#### 1. Güçlü Yönleri
- Kullanıcının halihazırda kullandığı gerçek brokerın (örn. IC Markets Raw ECN) birebir spread ve fiyat akışını yansıtır.
- `mt5.copy_rates_range` ve `mt5.copy_ticks_range` fonksiyonları hızlı Numpy array çıktıları verir.

#### 2. Neden 1. Tercih Olamaz? (Kritik Mimari Engeller)
1. **İşletim Sistemi Bağımlılığı:** `MetaTrader5` kütüphanesi yalnızca Windows üzerinde ve yerel bir MT5 Terminal uygulaması açıkken çalışır. Linux veya bulut ortamında çalıştırılamaz.
2. **Broker Saat Dilimi (EET) Karmaşası:** Çoğu MT5 sunucusu Doğu Avrupa Saati (EET = UTC+2 / UTC+3) kullanır. ABD ve Avrupa Yaz Saati (DST) geçiş tarihlerindeki farklılıklar nedeniyle geriye dönük UTC çevrimlerinde 1 saatlik kayma (DST off-by-one error) riski taşır.
3. **Sınırlı Tarihsel Derinlik:** Çoğu CFD brokerı sunucusunda 1–2 yıldan eski M1 veya tick verisini tutmaz; eski veriler boşluklu (gap) veya silinmiş olabilir.

---

### D. HISTDATA.COM VE DİĞER AÇIK ARŞİVLER
- **HistData.com:** Yıllık ve aylık sıkıştırılmış CSV arşivleri sunar. Ancak API desteği yoktur; manuel indirme veya web scraping gerektirir. Zaman damgaları ABD Doğu Saati (EST) formatındadır.
- **Polygon.io / TwelveData / AlphaVantage:** Ücretsiz katmanlarında ya Forex intraday geçmişi 1-2 yıl ile sınırlıdır ya da günlük 25-500 istek limiti nedeniyle çok yıllı veri indirmeyi imkansız kılar. Ücretli katmanları aylık $30-$100 maliyet yaratır.

---

## 4. QUANTITATIVE TRADING VE ICT/SMC İHTİYAÇLARI AÇISINDAN DEĞERLENDİRME

TRADERLAB araştırma hedefleri (ICT, SMC, Likidite, Fair Value Gap) sıradan bir teknik analiz botundan çok daha yüksek veri hassasiyeti gerektirir:

### A. Fitil (Wick) Hassasiyeti ve Likidite Süpürmeleri (Stop Hunts)
- **Problem:** SMC konseptlerinde en kritik sinyal, bir önceki gün/hafta en yükseğinin (BSL) veya en düşüğünün (SSL) birkaç pip süpürülüp anında geri çekilmesidir (Judas Swing / Turtle Soup).
- **Gereksinim:** Sağlayıcı veriyi yumuşatmamalı (no candle smoothing), aykırı değer filtresi (outlier filtering) ile fitil uçlarını budamamalıdır.
- **Değerlendirme:** Dukascopy'nin raw tick arşivi, ECN eşleşme motorunun gerçek en yüksek/en düşük işlemlerini milisaniyelik seviyede korur. FVG ve likidite analizleri için en yüksek doğruluğu sunar.

### B. Gerçek BID ve ASK Ayrımı (Spread Bozulması Önleme)
- Yalnızca "MID" (orta fiyat) veya "Close" üzerinden çalışan sistemler XAUUSD'de $0.20 - $0.50 arası yapay kaymalara yol açar.
- Alım emirleri (Buy Stop, Long TP, Short SL) **ASK** fiyatından gerçekleşir. Satım emirleri (Sell Stop, Short TP, Long SL) **BID** fiyatından gerçekleşir.
- Dukascopy ve OANDA, BID ve ASK serilerini ayrı ayrı sunarak TraderLab'in [`PriceType.BID`](file:///c:/trader/traderLab/traderlab/src/traderlab/core/types.py#L65) ve [`PriceType.ASK`](file:///c:/trader/traderLab/traderlab/src/traderlab/core/types.py#L66) portlarıyla %100 örtüşür.

### C. Seans Sınırları ve Günlük Kapanış (Daylight Saving / NY Cut)
- ICT metodolojisinde New York saat 17:00 (NY Midnight / NY Close) günlük mumun kapanışı kabul edilir.
- Dukascopy verisi saf UTC sunduğu için NY 17:00 kapanışı, yaz/kış saatine göre 21:00 UTC veya 22:00 UTC olarak matematiksel olarak deterministik biçimde hesaplanabilir.

---

## 5. MEVCUT MİMARİYLE UYUMLULUK ANALİZİ

TRADERLAB mevcut altyapısı seçilecek sağlayıcıyla tam bir arayüz uyumu içindedir:

1. **[`MarketDataProvider`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/ports.py#L80-L100) Protokolü:**
   Geliştirilecek olan `DukascopyDataProvider` adaptörü:
   - `provider_id = ProviderId("dukascopy")`
   - `fetch_candles(instrument_id, timeframe, start_utc, end_utc, price_type)` sözleşmesini doğrudan gerçekleştirecektir.
2. **[`DataSource`](file:///c:/trader/traderLab/traderlab/src/traderlab/domain/instruments/data_source.py) Kimliği:**
   - `id = DataSourceId("dukascopy:XAUUSD")`
   - `instrument_id = InstrumentId("XAUUSD")`
   - `provider_id = ProviderId("dukascopy")`
   - `provider_symbol = "XAUUSD"`
   - `tick_size_override = Decimal("0.01")`
   - `pip_size_override = Decimal("0.10")`
   - `price_precision_override = 2` (veya 3)
3. **[`PostgresCandleRepository`](file:///c:/trader/traderLab/traderlab/src/traderlab/infrastructure/database/repositories/candle_repository.py) Entegrasyonu:**
   İndirilen mumlar doğrudan `save_candles()` fonksiyonuna iletilerek transactional advisory locking ve metadata-aware idempotency kalkanı altında güvenle saklanabilecektir.
4. **Bitemporal PIT Uyumu:**
   Tarihsel arşivden indirilen barlar için `available_at = close_time + 1s` veya `None`, `availability_confidence = AvailabilityConfidence.UNKNOWN` (veya `RESEARCH_ESTIMATED`) olarak işaretlenerek geçmiş araştırması ile canlı gerçek zamanlı veri ayrımı korunacaktır.

---

## 6. NİHAİ SEÇİM VE TEKNİK GEREKÇELER

### BİRİNCİ SEÇİM (ÖNERİLEN): DUKASCOPY HISTORICAL DATA
**Teknik Gerekçeler:**
1. **Sıfır Maliyet ve Sıfır Bürokrasi:** Hesap açma, kimlik doğrulama, API token süresi dolması veya kota aşım ücreti yoktur.
2. **Eşsiz Tarihsel Kapsam:** 2003'ten bugüne kesintisiz 20 yılı aşkın XAUUSD tick ve dakika verisi mevcuttur.
3. **Gerçek ECN Likiditesi:** Perakende bir brokerın yapay fiyat filtrelerinden arınmış gerçek kurumsal banka kotasyonlarıdır.
4. **Platform Bağımsızlığı:** Saf Python (`lzma`, `struct`, `httpx`) ile dış bağımlılık olmadan çalışır; Linux ve bulut sunucularına taşınabilir.
5. **Hassasiyet:** Milisaniye düzeyinde UTC zaman damgası ve mikroskobik spread takibi sağlar.

### İKİNCİ SEÇİM (YEDEK VE DOĞRULAMA): OANDA REST API (v20)
**Teknik Gerekçeler:**
1. Dukascopy kotasyonlarını bağımsız bir perakende market-maker fiyat akışıyla kıyaslamak (cross-source sanity check) için idealdir.
2. M1/H1 barlarını önceden hesaplanmış olarak döndürdüğü için hızlı görsel doğrulama sağlar.

---

## 7. FAZ 1B-B İÇİN ÖNERİLEN PİLOT İNDİRME PLANI

Faz 1B-B aşamasında veritabanını ve sistemi test etmek için önerilen küçük ve kontrollü pilot kapsam:

- **Enstrüman:** `XAUUSD`
- **Tarih Aralığı:** **1 Ay** (Örnek: `2024-01-01 00:00:00 UTC` – `2024-02-01 00:00:00 UTC`)
- **Seçilen Zaman Dilimleri:**
  - `Timeframe.H1` (1 Saatlik barlar: ~500 bar)
  - `Timeframe.M15` (15 Dakikalık barlar: ~2.000 bar)
  - `Timeframe.M1` (1 Dakikalık barlar: ~30.000 bar)
- **Fiyat Türü:** Hem `PriceType.BID` hem de `PriceType.ASK` barları.
- **Doğrulama Adımları:**
  1. Ham Dukascopy `.bi5` veya M1 akışının indirilmesi.
  2. Decimal dönüşümü ($/ 1000.0) ve UTC normalizasyonu.
  3. `Candle` domain nesnelerine dönüştürülmesi.
  4. `PostgresCandleRepository.save_candles` ile `traderlab` veritabanına yazılması.
  5. Kaydedilen verinin bitemporal PIT ve `DISTINCT ON` sorgularıyla okunabilirliğinin test edilmesi.

---

## 8. FAZ 1B-B'YE GEÇİŞ ÖN KOŞULLARI

1. Kullanıcının bu araştırma raporundaki **Dukascopy** (1. Tercih) ve **OANDA** (2. Tercih) seçimini onaylaması.
2. Pilot veri indirme kapsamının (1 aylık XAUUSD H1/M15/M1) teyit edilmesi.
3. Faz 1B-B'de adaptör kodlarının (`DukascopyClient` ve `DukascopyMarketDataProvider`) Clean Architecture sınırları içinde geliştirilmesi.

---

## 9. MEVCUT KOD VE TEST DURUMU TEYİDİ

- Bu araştırma sürecinde **hiçbir kaynak kod dosyası değiştirilmemiştir**.
- Başlangıç ve bitiş test durumu: **168 passed in 1.57s (%100 Başarı)**.
- Ruff ve Mypy denetimleri: **0 hata, tamamen temiz**.
- Eski `borsa/` projesine veya canlı broker sistemlerine kesinlikle dokunulmamıştır.

**Nihai Durum:** **`READY_FOR_USER_DECISION`**
