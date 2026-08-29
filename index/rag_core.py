"""Reusable RAG retrieval core: query the built Chroma index from any project.

The index (chroma_db) and the local bge-m3 model are self-contained under
E:\\writing-rag, so other projects on this machine can query them directly
without copying the ~2.3 GB model or rebuilding the index.

Usage from another project:
    import sys
    sys.path.insert(0, r"E:\\writing-rag\\index")
    from rag_core import retrieve, dedupe_by_doi, summary_list

    hits = retrieve("mucin pool prognosis", k=8, mac_only=True)
    refs = dedupe_by_doi(hits)      # literature-level, deduped by DOI
    for r in summary_list(refs):
        print(r["title"], "|", r["journal"], "|", r["year"], "|", r["doi"])

All paths default to the writing-rag ones and can be overridden per call.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_DIR = ROOT / "index" / "chroma_db"
DEFAULT_MODEL_DIR = ROOT / ".model-cache" / "BAAI__bge-m3"
DEFAULT_MODEL_NAME = "BAAI/bge-m3"  # fallback hub name if local copy missing

os.environ.setdefault("HF_HOME", str(ROOT / ".model-cache"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(ROOT / ".model-cache" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from sentence_transformers import SentenceTransformer  # noqa: E402

import chromadb  # noqa: E402
from chromadb.config import Settings as ChromaSettings  # noqa: E402

# Module-level cache so repeated calls inside one process load the model once.
_MODEL = None
_COLLECTION = None


def _get_model(model_dir=None, device="cpu"):
    global _MODEL
    if _MODEL is None:
        path = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        model_path = str(path) if path.exists() else DEFAULT_MODEL_NAME
        _MODEL = SentenceTransformer(model_path, device=device)
    return _MODEL


def _get_collection(db_dir=None):
    global _COLLECTION
    if _COLLECTION is None:
        path = Path(db_dir) if db_dir else DEFAULT_DB_DIR
        client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
        )
        _COLLECTION = client.get_collection("papers")
    return _COLLECTION


def retrieve(query, k=8, mac_only=False, db_dir=None, model_dir=None, device="cpu"):
    """Semantic search over the built index.

    Returns a list of dicts (rank, score, source, mac, title, year, journal,
    doi, authors, section, snippet) sorted by score descending.
    mac_only=True restricts to MAC-classified chunks (metadata filter).
    """
    model = _get_model(model_dir, device)
    collection = _get_collection(db_dir)
    q = model.encode([query], normalize_embeddings=True)
    where = {"mac": "yes"} if mac_only else None
    res = collection.query(query_embeddings=q, n_results=k, where=where)

    out = []
    for i, (doc, meta, dist) in enumerate(
        zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ):
        out.append(
            {
                "rank": i + 1,
                "score": round(1 - dist, 4),  # cosine similarity (normalized)
                "source": meta["source"],
                "mac": meta["mac"],
                "title": meta["title"],
                "year": meta["year"],
                "journal": meta["journal"],
                "doi": meta["doi"],
                "authors": meta["authors"],
                "section": meta["section"],
                "snippet": doc[:400],
            }
        )
    return out


def dedupe_by_doi(results):
    """Merge hits of the same paper into one literature-level entry.

    Keyed by lowercased DOI; entries without DOI fall back to the source
    filename. Keeps the highest-score hit's fields, accumulates section
    locations and counts hits (n_hits). Returns entries sorted by score.
    """
    merged = {}
    for r in results:
        key = (r.get("doi") or "").strip().lower() or r["source"]
        if key not in merged:
            entry = dict(r)
            entry["sections"] = [r["section"]] if r.get("section") else []
            entry["n_hits"] = 1
            merged[key] = entry
        else:
            m = merged[key]
            if r["score"] > m["score"]:
                for f in ("title", "year", "journal", "doi", "authors", "source", "mac"):
                    if r.get(f):
                        m[f] = r[f]
                m["score"] = r["score"]
            if r.get("section") and r["section"] not in m["sections"]:
                m["sections"].append(r["section"])
            m["n_hits"] += 1
    return sorted(merged.values(), key=lambda x: -x["score"])


def summary_list(entries):
    """Structured citation-ready list, without snippet noise.

    Each entry: title, year, journal, doi, authors, mac, source, sections,
    n_hits. `entries` may be raw retrieve() output or dedupe_by_doi() output.
    """
    return [
        {
            "title": e["title"],
            "year": e["year"],
            "journal": e["journal"],
            "doi": e["doi"],
            "authors": e["authors"],
            "mac": e["mac"],
            "source": e["source"],
            "sections": e.get("sections", [e["section"]] if e.get("section") else []),
            "n_hits": e.get("n_hits", 1),
        }
        for e in entries
    ]
