# HuMetric — Yerel LLM Denemesi, 2. Makine (Windows, RTX 4050)

> Durum: **planlandı, henüz başlanmadı.** 26 Ağustos 2026.

Bu dosya oturumlar arası (`/loop`) kalıcı durum/talimat dosyasıdır. Yeni bir
oturum önce burayı okur, "Durum" satırındaki adımdan devam eder. Her adım
tamamlandığında ilgili checkbox `[x]` yapılır ve altına 1-2 cümlelik somut
sonuç notu eklenir — yalnızca doğrulanmış, olgusal notlar; uydurma iddia yok.

## Bağlam

[`docs/plans/yerel-llm-denemesi.md`](yerel-llm-denemesi.md) 16GB Apple M2
üzerinde 6 modeli (qwen2.5:7b, llama3.1:8b, granite3.3:8b, mistral:7b,
gemma2:9b, phi3.5:3.8b) test ediyor/edecek. Bu dosya aynı karşılaştırmayı
**farklı donanımda** ("Monster" marka Windows laptop, 32GB RAM, GTX/RTX 4050
laptop GPU) tekrarlamak için ayrı bir tur — amaç GPU hızlanmasının (CUDA)
sonucu ve model seçimini nasıl değiştirdiğini görmek, iki makine raporunu
karşılaştırmak.

**Not:** GPU adı kullanıcıdan "GTX 4050" olarak geldi ama NVIDIA'nın güncel
laptop GPU serisinde bu isimde bir kart yok — muhtemelen **RTX 4050**
(laptop, tipik olarak ~6GB VRAM). Adım 0'da `nvidia-smi` ile gerçek model ve
VRAM teyit edilecek, yanlışsa burada düzeltilecek.

## Neden ayrı tur

- **VRAM asıl kısıt.** RTX 4050 laptop tipik olarak 6GB VRAM'e sahip. Test
  edilen modellerin Q4 boyutları (2.2-5.4GB) VRAM'e sığar → GPU'da tam
  hızlanma beklenir (CPU/Metal'e göre belirgin daha hızlı). 13B+ modeller
  kısmi CPU offload'a düşüp yavaşlayabilir — bu turda hâlâ kapsam dışı,
  6GB VRAM sınırını aşan modellerle uğraşmıyoruz.
- **32GB sistem RAM'i** M2'nin 16GB'ından fazla headroom verir, ama darboğaz
  GPU VRAM'i olduğu için bu RAM farkı muhtemelen sonucu değiştirmez —
  gözlemlenecek, varsayılmayacak.
- Aynı 4 test sinyali, aynı script (`scripts/local_llm_ollama_bench.py`),
  aynı `ExtractionResult` şeması — yalnızca runtime/donanım değişiyor, kod
  tabanına dokunulmuyor (M2 planındaki kısıtlar burada da geçerli).

## Adımlar

- [ ] **Adım 0 — donanım teyidi**
  - Windows makinede `nvidia-smi` çalıştırılıp gerçek GPU adı + VRAM miktarı
    doğrulanacak (kullanıcı "GTX 4050" dedi, muhtemelen RTX 4050 — teyit
    edilecek). Sistem RAM'i (`wmic memorychip` veya Görev Yöneticisi) ve
    CPU teyit edilecek.

- [ ] **Adım 1 — Ollama Windows kurulumu**
  - `ollama.com`'dan Windows installer veya `winget install Ollama.Ollama`.
  - CUDA desteğinin otomatik algılandığı `ollama serve` loglarından teyit
    edilecek (GPU offload log satırı).

- [ ] **Adım 2 — aynı 6 modeli çek**
  - `ollama pull qwen2.5:7b llama3.1:8b granite3.3:8b mistral:7b gemma2:9b
    phi3.5:3.8b` — M2 planıyla birebir aynı liste, karşılaştırılabilirlik
    için model seçimi değiştirilmiyor.
  - Disk alanına dikkat: 6 model ~26.5GB — Windows makinenin boş disk alanı
    önceden kontrol edilecek (M2'de bu adımda disk dolmuştu, tekrarlanmasın).

- [ ] **Adım 3 — repo'yu Windows makineye taşı, bench scriptini çalıştır**
  - `humetric` reposu clone edilir, `.venv` kurulur (`pip install -e ".[dev]"`
    yeterli — bench script yalnızca `httpx`, `pydantic`, ve `humetric.schema`/
    `humetric.agents.extractor`'a bağımlı, DB/API gerekmiyor).
  - `python scripts/local_llm_ollama_bench.py` (tüm 6 model, `OLLAMA_MODELS`
    env var'ı ile ya da script varsayılanını genişletip).
  - Çıktılar `scripts/output/local_llm_bench_<model>_win.json` gibi ayrı bir
    son ekle kaydedilecek (M2 çıktılarıyla karışmasın, iki makineyi yan yana
    karşılaştırabilelim).

- [ ] **Adım 4 — karşılaştırma tablosu (M2 vs RTX 4050)**
  - Her model için: JSON geçerlilik oranı (donanımdan bağımsız olmalı, sapma
    varsa ilginç bir bulgu), ortalama latency (asıl fark burada beklenir),
    injection direnci (donanımdan bağımsız, model davranışı — ama yine de
    iki makinede de test edilip tutarlılık teyit edilecek).

- [ ] **Adım 5 — karar notu**
  - İki makine raporu birleştirilip nihai "hangi model + hangi donanım"
    önerisi yazılacak. HuMetric'in self-host hikâyesi için: "16GB RAM'de bile
    X çalışır, GPU'lu makinede Y kat daha hızlı" gibi somut, ölçülmüş bir
    iddia hedefleniyor — pazarlama abartısı değil.

## Doğrulama

- `nvidia-smi` çıktısı gerçek GPU/VRAM'i teyit eder.
- `ollama list` Windows makinede 6 modelin de indiğini gösterir.
- `python scripts/local_llm_ollama_bench.py` hatasız tamamlanır, her model
  için `scripts/output/`'a bir JSON dosyası yazar.
- Bu dosyanın "Adım 4-5" bölümü M2 sonuçlarıyla yan yana karşılaştırmalı bir
  özetle doldurulur.
