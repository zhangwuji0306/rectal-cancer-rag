"""Build a local Chroma vector index over converted/*.md.

Embeddings : BAAI/bge-m3 (local, CPU), markdown-heading-aware chunking.
Storage    : Chroma persistent DB at index/chroma_db, collection "papers".
Metadata   : merged from .claude/tmp/mapping.json (Zotero/CSV) + alias table
             for known supplementary / split files.

Idempotent: files whose sha256 is unchanged are skipped (incremental).
Rebuild all with --force.

Usage:
    python index/build_index.py            # incremental
    python index/build_index.py --force    # full rebuild
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONVERTED = ROOT / "converted"
INDEX_DIR = ROOT / "index"
CHROMA_DIR = INDEX_DIR / "chroma_db"
MANIFEST_PATH = INDEX_DIR / "manifest.json"
MODEL_NAME = "BAAI/bge-m3"
LOCAL_MODEL_DIR = ROOT / ".model-cache" / "BAAI__bge-m3"  # pre-downloaded local copy
CHUNK_MAX_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 80
MIN_CHUNK_TOKENS = 16  # drop tiny noise fragments (title-page art, etc.)

# Keep the ~2.3 GB model cache inside the workspace (self-contained).
os.environ.setdefault("HF_HOME", str(ROOT / ".model-cache"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(ROOT / ".model-cache" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from sentence_transformers import SentenceTransformer  # noqa: E402

import torch  # noqa: E402

import chromadb  # noqa: E402
from chromadb.config import Settings as ChromaSettings  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------------------
# Metadata lookup: converted md stem -> mapping.json entry
# ---------------------------------------------------------------------------

ALIASES = {
    # converted stem -> (lookup kind, value)
    "ESGAR202601 PARTI": ("folder", "7TE8XYFI"),
    "ESGAR202601 PARTII": ("folder", "FZQ7B3VD"),
    "zrac039_supplementary_data": ("folder", "LMF8QQ6J"),            # Enblad 2022 supp
    "cancers-1559433-supplementary": ("doi", "10.3390/cancers14051297"),  # Bong 2022 supp
    "NIHMS2115008-supplement-Supplement": ("doi", "10.1007/s00330-025-11967-6"),  # Javed-Tayyab 2025 supp
}


def load_meta():
    by_stem = {}
    by_folder = {}
    by_doi = {}
    for mapping_path in (
        ROOT / ".claude" / "tmp" / "mapping.json",        # MAC corpus (first: wins DOI conflicts)
        ROOT / ".claude" / "tmp" / "mapping_pmid.json",   # PMID batch (generated from 索引信息.csv)
    ):
        if not mapping_path.exists():
            continue
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        for f in mapping["files"]:
            fn = f.get("filename") or ""
            if fn:
                by_stem[Path(fn).stem] = f
            if f.get("folder"):
                by_folder[f["folder"]] = f
            doi = (f.get("doi") or "").strip().lower()
            if doi:
                by_doi.setdefault(doi, f)
    return by_stem, by_folder, by_doi


def meta_for(rel_name, by_stem, by_folder, by_doi):
    """Return (meta_dict, is_supp_or_alias) for a converted file."""
    stem = Path(rel_name).stem
    if stem in ALIASES:
        kind, value = ALIASES[stem]
        f = by_folder.get(value) if kind == "folder" else by_doi.get(value.lower())
        if f:
            return _entry_meta(f), True
    f = by_stem.get(stem)
    if f:
        return _entry_meta(f), False
    return {
        "title": stem,
        "year": "",
        "journal": "",
        "doi": "",
        "authors": "",
        "mac": "unknown",
        "folder": "",
        "evidence": "",
    }, False


def _entry_meta(f):
    return {
        "title": f.get("title") or Path(f.get("filename") or "?").stem,
        "year": str(f.get("year") or ""),
        "journal": f.get("journal") or "",
        "doi": f.get("doi") or "",
        "authors": f.get("author") or "",
        "mac": "yes" if f.get("mac") else "no",
        "folder": f.get("folder") or "",
        "evidence": f.get("evidence") or "",
    }


# ---------------------------------------------------------------------------
# Chunking (heading-aware, ~800 tokens, paragraph granularity)
# ---------------------------------------------------------------------------

def chunk_markdown(text, tokenizer, max_tokens=CHUNK_MAX_TOKENS, overlap=CHUNK_OVERLAP_TOKENS):
    def count_tokens(s):
        return len(tokenizer.encode(s, add_special_tokens=False))

    def split_sentences(para):
        units = [u.strip() for u in re.split(r"(?<=[.!?])\s+|\n", para) if u.strip()]
        out, buf, bt = [], [], 0
        for u in units:
            ut = count_tokens(u)
            while ut > max_tokens:  # pathological: iteratively cut to token budget
                cpt = max(1.0, len(u) / max(1, ut))  # chars per token estimate
                u = u[: int(max_tokens * cpt * 0.9)]
                ut = count_tokens(u)
            if buf and bt + ut > max_tokens:
                out.append(" ".join(buf))
                buf, bt = [], 0
            buf.append(u)
            bt += ut
        if buf:
            out.append(" ".join(buf))
        return out

    lines = text.splitlines()
    chunks = []
    heading_stack = []  # (level, title)
    buf = []

    def flush():
        nonlocal buf
        if not buf:
            return
        body = "\n".join(buf).strip()
        buf = []
        if not body:
            return
        section = " > ".join(t for _, t in heading_stack)
        if count_tokens(body) < MIN_CHUNK_TOKENS:  # drop noise fragments
            return
        if count_tokens(body) <= max_tokens:
            chunks.append({"text": body, "section": section})
            return
        paras = [p.strip() for p in body.split("\n\n") if p.strip()]
        acc, acc_toks = [], 0
        for p in paras:
            pt = count_tokens(p)
            if pt > max_tokens:
                if acc:
                    chunks.append({"text": "\n\n".join(acc), "section": section})
                    acc, acc_toks = [], 0
                for sub in split_sentences(p):
                    chunks.append({"text": sub, "section": section})
                continue
            if acc and acc_toks + pt > max_tokens:
                chunks.append({"text": "\n\n".join(acc), "section": section})
                carry, ct = [], 0  # keep tail paragraphs as overlap
                for pp in reversed(acc):
                    t = count_tokens(pp)
                    if ct + t > overlap:
                        break
                    carry.insert(0, pp)
                    ct += t
                acc, acc_toks = carry, ct
            acc.append(p)
            acc_toks += pt
        if acc:
            chunks.append({"text": "\n\n".join(acc), "section": section})

    in_fence = False
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or stripped.startswith("![") or stripped.startswith("<!--"):
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            flush()
            lvl, title = len(m.group(1)), m.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= lvl:
                heading_stack.pop()
            heading_stack.append((lvl, title))
        else:
            buf.append(ln)
    flush()
    return chunks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild everything")
    ap.add_argument("--limit", type=int, default=0, help="only process first N files (debug)")
    ap.add_argument("--device", default="auto", help="cpu | cuda | auto (default)")
    args = ap.parse_args()

    INDEX_DIR.mkdir(exist_ok=True)
    manifest = {"version": 1, "model": MODEL_NAME, "files": {}}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    torch.set_num_threads(max(1, os.cpu_count() or 8))
    print(f"torch threads: {torch.get_num_threads()}")

    by_stem, by_folder, by_doi = load_meta()
    md_files = sorted(CONVERTED.glob("*.md"))
    todo = []
    skipped = 0
    for md in md_files:
        rel = md.name
        h = hashlib.sha256(md.read_bytes()).hexdigest()
        if not args.force and manifest["files"].get(rel, {}).get("sha256") == h:
            skipped += 1
            continue
        todo.append((md, h))
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print(f"No new files ({skipped} already indexed). Use --force to rebuild.")
        return

    if args.device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        device = args.device
    print(f"device: {device}")
    print(f"Loading model {MODEL_NAME} (local dir: {LOCAL_MODEL_DIR}) ...")
    model_path = str(LOCAL_MODEL_DIR) if LOCAL_MODEL_DIR.exists() else MODEL_NAME
    model = SentenceTransformer(model_path, device=device)
    if device == "cuda":
        model.half()  # fp16 to fit the 2 GB laptop GPU
        torch.cuda.empty_cache()
    tokenizer = model[0].tokenizer

    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR), settings=ChromaSettings(anonymized_telemetry=False)
    )
    collection = client.get_or_create_collection(
        "papers", metadata={"hnsw:space": "cosine"}
    )

    t0 = time.time()
    total_chunks = 0
    done_chunks = 0
    for md, h in todo:
        rel = md.name
        meta, is_alias = meta_for(rel, by_stem, by_folder, by_doi)
        text = md.read_text(encoding="utf-8", errors="replace")
        chunks = chunk_markdown(text, tokenizer)
        if not chunks:
            print(f"  SKIP (no text): {rel}")
            manifest["files"][rel] = {"sha256": h, "chunks": 0, "mac": meta["mac"]}
            continue
        docs = [c["text"] for c in chunks]
        sections = [c["section"] for c in chunks]
        ids = [f"{h[:12]}-{i:03d}" for i in range(len(chunks))]
        metas = []
        for i, c in enumerate(chunks):
            m = dict(meta)
            m.update({"source": rel, "section": sections[i], "chunk": i, "supp": "yes" if is_alias else "no"})
            metas.append(m)
        collection.delete(where={"source": rel})  # replace stale chunks of this file
        batch = 16 if device == "cuda" else 48
        embeddings = model.encode(docs, batch_size=batch, normalize_embeddings=True, show_progress_bar=False)
        collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
        manifest["files"][rel] = {"sha256": h, "chunks": len(chunks), "mac": meta["mac"]}
        total_chunks += len(chunks)
        done_chunks += len(chunks)
        tag = f"[{meta['mac']}]" if meta["mac"] != "unknown" else "[?]"
        print(f"  {tag} {rel}  -> {len(chunks)} chunks (cum {done_chunks})", flush=True)

    manifest["built_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    manifest["total_chunks"] = total_chunks
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nDone: {len(todo)} files, {total_chunks} chunks, {skipped} skipped, "
          f"{time.time() - t0:.0f}s. Index at {CHROMA_DIR}")


if __name__ == "__main__":
    main()
