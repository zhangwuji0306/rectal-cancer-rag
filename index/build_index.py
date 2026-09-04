"""Build a local Chroma vector index over converted Markdown.

Embeddings : BAAI/bge-m3 (local, CPU), markdown-heading-aware chunking.
Storage    : Chroma persistent DB at index/chroma_db, collection "papers".
Metadata   : merged from .claude/tmp/mapping.json (Zotero/CSV) + alias table
             for known supplementary / split files.

Idempotent: files whose sha256 is unchanged are skipped (incremental).
Both converted/*.md and converted/已入库/*.md are inputs; the archive move does
not cause a second conversion or embedding pass.  Rebuild all with --force.

Duplicate content policy: md files that are sha-identical (e.g. one paper
converted under both a PMID_* name and a legacy Zotero/Word name, or the same
article downloaded under several PMIDs) hold identical deterministic chunk
ids, so Chroma can physically store them only once.  Each duplicate group is
indexed under one canonical name (PMID_* names preferred, else the smallest
name); the other files of the group get a chunks=0 ledger entry (``dup_of``)
and are never upserted.  Manifest totals therefore equal real Chroma rows even
on a --force rebuild.

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
ARCHIVE = CONVERTED / "已入库"
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
        "pub_type": "",
        "license": "unverified",
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
        "pub_type": f.get("pub_type") or f.get("pubtype") or "",
        "license": f.get("license") or "unverified",
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

    def split_token_windows(value):
        """Split an overlong unit by token windows without dropping its tail."""
        token_ids = tokenizer.encode(value, add_special_tokens=False)
        if len(token_ids) <= max_tokens:
            return [value]
        step = max_tokens - overlap
        if step <= 0:
            raise ValueError("overlap must be smaller than max_tokens")
        out = []
        start = 0
        while start < len(token_ids):
            end = min(start + max_tokens, len(token_ids))
            piece = tokenizer.decode(
                token_ids[start:end],
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            ).strip()
            if piece:
                out.append(piece)
            if end == len(token_ids):
                break
            start += step
        return out

    def split_sentences(para):
        units = [u.strip() for u in re.split(r"(?<=[.!?])\s+|\n", para) if u.strip()]
        out, buf, bt = [], [], 0
        for u in units:
            ut = count_tokens(u)
            if ut > max_tokens:
                if buf:
                    out.append(" ".join(buf))
                    buf, bt = [], 0
                out.extend(split_token_windows(u))
                continue
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


def markdown_files():
    """Return one path per Markdown filename; top-level wins on collision."""
    by_name = {}
    for base in (CONVERTED, ARCHIVE):
        if not base.exists():
            continue
        for path in sorted(base.glob("*.md")):
            by_name.setdefault(path.name, path)
    return [(name, by_name[name]) for name in sorted(by_name)]


def _canonical_key(name):
    """Duplicate-group ordering: PMID_* names win, then plain lexicographic."""
    return (0 if name.startswith("PMID_") else 1, name)


def canonical_rel(group):
    """Pick the canonical filename of a sha-identical duplicate group."""
    return sorted(group, key=_canonical_key)[0]


def _source_ids(collection, source):
    rows = collection.get(where={"source": source}, include=["metadatas"])
    return list(rows.get("ids") or [])


def replace_source(collection, source, ids, documents, metadatas, embeddings):
    """Write new chunks before removing stale ones for this source.

    If embedding or the write fails, the previous chunks remain available.  A
    deterministic id plus ``upsert`` also makes an interrupted retry repair
    the harmless duplicate-free state.
    """
    old_ids = _source_ids(collection, source)
    if documents:
        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        stale_ids = [item for item in old_ids if item not in set(ids)]
    else:
        stale_ids = old_ids
    if stale_ids:
        collection.delete(ids=stale_ids)


def collection_sources(collection):
    """Return all source names currently present in Chroma."""
    count = collection.count()
    if not count:
        return set()
    sources = set()
    batch = 10000
    # A single get(limit=count) exceeds SQLite's variable limit on large
    # collections (56k+ chunks); page through offsets like reconcile_manifest.
    for offset in range(0, count, batch):
        rows = collection.get(limit=batch, offset=offset, include=["metadatas"])
        for meta in (rows.get("metadatas") or []):
            source = (meta or {}).get("source")
            if source:
                sources.add(source)
    return sources


def clear_collection(collection):
    """Delete every row in a collection in bounded batches."""
    while True:
        rows = collection.get(limit=10000, include=["metadatas"])
        ids = rows.get("ids") or []
        if not ids:
            return
        collection.delete(ids=ids)


def write_manifest_atomic(manifest):
    """Atomically publish the manifest after all Chroma writes succeed."""
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)


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
    if MANIFEST_PATH.exists() and not args.force:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest.setdefault("files", {})

    torch.set_num_threads(max(1, os.cpu_count() or 8))
    print(f"torch threads: {torch.get_num_threads()}")

    by_stem, by_folder, by_doi = load_meta()
    file_items = markdown_files()
    current_names = {name for name, _ in file_items}

    # One pass over the corpus: group files by content sha so duplicate
    # content is never upserted twice (identical content -> identical chunk
    # ids -> the second write would silently re-own the first file's rows).
    file_hash = {}
    group_by_sha = {}
    for rel, md in file_items:
        h = hashlib.sha256(md.read_bytes()).hexdigest()
        file_hash[rel] = h
        group_by_sha.setdefault(h, []).append(rel)
    dup_skips = set()
    canonical_of = {}
    for h, rels in group_by_sha.items():
        if len(rels) < 2:
            continue
        canon = canonical_rel(rels)
        canonical_of[h] = canon
        dup_skips.update(r for r in rels if r != canon)

    todo = []
    skipped = 0
    for rel, md in file_items:
        h = file_hash[rel]
        entry = manifest["files"].get(rel, {})
        if not args.force and entry.get("sha256") == h:
            if rel in dup_skips and entry.get("chunks", 0) != 0:
                # Duplicate content lives under its canonical twin; keep this
                # entry so the sha skip stays idempotent, but stop counting
                # phantom chunks in the manifest ledger.
                meta, _ = meta_for(rel, by_stem, by_folder, by_doi)
                manifest["files"][rel] = {
                    "sha256": h,
                    "chunks": 0,
                    "mac": meta["mac"],
                    "dup_of": canonical_of[h],
                }
            skipped += 1
            continue
        todo.append((md, h))
    if args.limit:
        todo = todo[: args.limit]
    stale_manifest = set(manifest["files"]) - current_names
    if not todo and not stale_manifest and not args.force:
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

    if args.force:
        clear_collection(collection)
        manifest = {"version": 1, "model": MODEL_NAME, "files": {}}
        todo = [(md, file_hash[rel])
                for rel, md in file_items]
        if args.limit:
            todo = todo[: args.limit]
    else:
        # Remove manifest entries and Chroma rows whose Markdown source was
        # removed.  Archive moves retain the filename and therefore remain
        # the same logical source.
        for source in sorted(stale_manifest | (collection_sources(collection) - current_names)):
            replace_source(collection, source, [], [], [], None)
            manifest["files"].pop(source, None)

    t0 = time.time()
    total_chunks = 0
    done_chunks = 0
    for md, h in todo:
        rel = md.name
        if rel in dup_skips:
            meta, is_alias = meta_for(rel, by_stem, by_folder, by_doi)
            manifest["files"][rel] = {
                "sha256": h,
                "chunks": 0,
                "mac": meta["mac"],
                "dup_of": canonical_of[h],
            }
            print(f"  DUP (indexed as {canonical_of[h]}): {rel}", flush=True)
            continue
        meta, is_alias = meta_for(rel, by_stem, by_folder, by_doi)
        text = md.read_text(encoding="utf-8", errors="replace")
        chunks = chunk_markdown(text, tokenizer)
        if not chunks:
            replace_source(collection, rel, [], [], [], None)
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
        batch = 16 if device == "cuda" else 48
        embeddings = model.encode(docs, batch_size=batch, normalize_embeddings=True, show_progress_bar=False)
        replace_source(collection, rel, ids, docs, metas, embeddings)
        manifest["files"][rel] = {"sha256": h, "chunks": len(chunks), "mac": meta["mac"]}
        total_chunks += len(chunks)
        done_chunks += len(chunks)
        tag = f"[{meta['mac']}]" if meta["mac"] != "unknown" else "[?]"
        print(f"  {tag} {rel}  -> {len(chunks)} chunks (cum {done_chunks})", flush=True)

    manifest["built_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    manifest["total_chunks"] = sum(
        item.get("chunks", 0) for item in manifest["files"].values()
    )
    write_manifest_atomic(manifest)
    print(f"\nDone: {len(todo)} files, {total_chunks} chunks, {skipped} skipped, "
          f"{time.time() - t0:.0f}s. Index at {CHROMA_DIR}")


if __name__ == "__main__":
    main()
