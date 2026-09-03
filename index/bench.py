"""Isolate the bottleneck: encode speed vs chroma add speed."""
import sys, time, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / ".model-cache"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(ROOT / ".model-cache" / "huggingface"))

import torch
torch.set_num_threads(8)
print("threads:", torch.get_num_threads(), flush=True)

from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.config import Settings as ChromaSettings

MODEL_DIR = ROOT / ".model-cache" / "BAAI__bge-m3"
device = "cuda" if torch.cuda.is_available() else "cpu"
t0 = time.time()
model = SentenceTransformer(MODEL_DIR, device=device)
if device == "cuda":
    model.half()
print(f"model load on {device}: {time.time()-t0:.1f}s", flush=True)

# 20 pseudo-chunks of ~800 tokens each
base = ("Neoadjuvant chemoradiotherapy followed by total mesorectal excision is the standard of care for locally "
        "advanced rectal cancer. Mucinous adenocarcinoma is a distinct histologic subtype accounting for 10-15% of "
        "rectal cancers. On T2-weighted MRI, mucinous components appear as high signal intensity regions with signal "
        "intensity ratios relative to the mesorectal fat of at least 1. The proportion of mucinous components, mucin "
        "pools, signal intensity ratios, extramural depth of invasion, and lymph node short axis diameter are "
        "important imaging biomarkers. Pathologic response is assessed by tumor regression grading systems. ") * 6
texts = [base + f"Sentence number {i} with unique content about prognosis, overall survival, disease free survival, "
         f"local recurrence and distant metastasis rates in patients with mucinous rectal cancer treated with "
         f"neoadjuvant therapy and surgery." for i in range(20)]
print(f"chunk texts ready ({len(texts)}), avg tokens: "
      f"{sum(len(model[0].tokenizer.encode(t, add_special_tokens=False)) for t in texts)//len(texts)}", flush=True)

t0 = time.time()
emb = model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
dt = time.time() - t0
print(f"encode 20 chunks: {dt:.1f}s -> {dt/20*1000:.0f} ms/chunk ({20/dt:.2f} chunks/s)", flush=True)

# chroma part
client = chromadb.PersistentClient(path=str(ROOT / "index" / "_bench_db"), settings=ChromaSettings(anonymized_telemetry=False))
col = client.get_or_create_collection("bench", metadata={"hnsw:space": "cosine"})
t0 = time.time()
col.add(ids=[f"b{i}" for i in range(20)], documents=texts,
        metadatas=[{"source": "bench", "mac": "yes"}]*20, embeddings=emb.tolist())
print(f"chroma add 20 chunks: {time.time()-t0:.1f}s", flush=True)
t0 = time.time()
col.delete(where={"source": "bench"})
print(f"chroma delete: {time.time()-t0:.1f}s", flush=True)
