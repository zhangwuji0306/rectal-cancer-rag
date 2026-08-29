"""Reconcile manifest.json with the actual Chroma ``papers`` collection.

The normal build/ingest paths update the manifest at the end of a run.  If two
writers overlap, the last writer can persist an older in-memory snapshot.  This
utility treats Chroma as the source of truth, reconstructs one manifest entry
per stored ``source``, and recalculates each source file's SHA-256 from the
Markdown on disk.

Usage (from E:\\writing-rag):
    .venv\\Scripts\\python.exe index\\reconcile_manifest.py --dry-run
    .venv\\Scripts\\python.exe index\\reconcile_manifest.py

Only sources that actually have chunks in Chroma are written.  Files in
converted\\已入库 with identical content but no independent Chroma source are
therefore not falsely marked as indexed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONVERTED = ROOT / "converted"
ARCHIVE = CONVERTED / "已入库"
INDEX_DIR = ROOT / "index"
CHROMA_DIR = INDEX_DIR / "chroma_db"
MANIFEST_PATH = INDEX_DIR / "manifest.json"
BACKUP_DIR = ROOT / ".claude" / "tmp" / "manifest-backups"
DEFAULT_MODEL = "BAAI/bge-m3"
BATCH_SIZE = 2000

os.environ.setdefault("HF_HOME", str(ROOT / ".model-cache"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(ROOT / ".model-cache" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import chromadb  # noqa: E402
from chromadb.config import Settings as ChromaSettings  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_file(source: str) -> Path | None:
    """Find a source in the two supported Markdown locations."""
    for base in (CONVERTED, ARCHIVE):
        candidate = (base / source).resolve()
        try:
            candidate.relative_to(base.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def collect_sources(collection):
    """Return source -> {chunks, mac} from all Chroma metadata rows."""
    total = collection.count()
    sources = {}
    for offset in range(0, total, BATCH_SIZE):
        rows = collection.get(
            include=["metadatas"], limit=BATCH_SIZE, offset=offset
        )["metadatas"]
        for meta in rows:
            source = (meta or {}).get("source")
            if not source:
                raise RuntimeError(f"Chroma row at offset {offset} has no source")
            entry = sources.setdefault(
                source,
                {"chunks": 0, "mac": (meta or {}).get("mac", "unknown")},
            )
            entry["chunks"] += 1
            current_mac = (meta or {}).get("mac", "unknown")
            if entry["mac"] != current_mac:
                raise RuntimeError(
                    f"inconsistent mac metadata for source {source!r}: "
                    f"{entry['mac']!r} vs {current_mac!r}"
                )
    return sources, total


def build_manifest(sources, collection_count, old_manifest):
    files = {}
    missing = []
    for source in sorted(sources):
        path = source_file(source)
        if path is None:
            missing.append(source)
            continue
        files[source] = {
            "sha256": sha256(path),
            "chunks": sources[source]["chunks"],
            "mac": sources[source]["mac"],
        }
    if missing:
        sample = ", ".join(missing[:10])
        raise RuntimeError(
            f"{len(missing)} Chroma sources have no Markdown file under "
            f"converted/ or converted/已入库 (sample: {sample})"
        )
    chunk_sum = sum(item["chunks"] for item in files.values())
    if chunk_sum != collection_count:
        raise RuntimeError(
            f"chunk count changed during scan: metadata sum={chunk_sum}, "
            f"collection.count()={collection_count}"
        )
    return {
        "version": old_manifest.get("version", 1),
        "model": old_manifest.get("model", DEFAULT_MODEL),
        "files": files,
        "total_chunks": chunk_sum,
        "built_at": old_manifest.get("built_at", ""),
        "reconciled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def main():
    parser = argparse.ArgumentParser(description="Reconcile manifest.json from Chroma")
    parser.add_argument("--dry-run", action="store_true", help="validate and print counts only")
    parser.add_argument("--no-backup", action="store_true", help="do not save the old manifest")
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(MANIFEST_PATH)
    old_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR), settings=ChromaSettings(anonymized_telemetry=False)
    )
    collection = client.get_collection("papers")
    sources, collection_count = collect_sources(collection)
    manifest = build_manifest(sources, collection_count, old_manifest)

    old_files = old_manifest.get("files", {})
    print(
        f"Chroma: {len(sources)} sources / {collection_count} chunks\n"
        f"manifest: {len(old_files)} sources / "
        f"{old_manifest.get('total_chunks', 0)} chunks\n"
        f"reconciled: {len(manifest['files'])} sources / "
        f"{manifest['total_chunks']} chunks"
    )
    if args.dry_run:
        print("DRY-RUN: manifest not changed")
        return

    if not args.no_backup:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = BACKUP_DIR / f"manifest-{stamp}.json"
        shutil.copy2(MANIFEST_PATH, backup)
        print(f"backup: {backup}")

    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)
    print(f"written: {MANIFEST_PATH}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - concise CLI failure
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
