#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch convert papers PDF/DOCX -> Markdown using MinerU.

Enhanced version:
- Idempotent by output stem: if converted/<stem>.md or converted/已入库/<stem>.md
  already exists -> skip (immune to Zotero re-export path changes and archive moves)
- Files > 10 MB use `extract` mode (requires MINERU_TOKEN env or --token)
- flash-extract failure is retried once with `extract` mode
- Known duplicates / already-covered documents are skipped (see SKIP_PREFIXES)
Output: flat converted/*.md (no subfolders).
"""

import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from datetime import datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "papers"
OUTPUT = PROJECT / "converted"
FAILED = PROJECT / "flash-failed"
TRACKER_PATH = PROJECT / ".claude" / "conversion-tracker.json"

FLASH_LIMIT_MB = 10.0  # flash-extract file size limit
EXTRACT_TIMEOUT = "7200"  # seconds for extract mode

# Stems to skip, with reason. Matching is prefix-based on the file stem.
SKIP_PREFIXES = [
    ("ESGAR Rectal Imaging Guideline Group",
     "converted/ 已有 ESGAR202601 PARTI/PARTII.md（同内容，2026-07-14 转换）"),
    ("Melis 等 - 2010 - Gene expression profiling of colorectal mucinous adenocarcinomas",
     "与 CL55BBYQ/Melis Gene Expression Profiling (大写) 同 DOI，疑似重复副本，未转换"),
]

OUTPUT.mkdir(parents=True, exist_ok=True)
FAILED.mkdir(parents=True, exist_ok=True)

# Optional runtime flags:
#   --max-run-minutes <n>  stop after finishing the current file once n minutes elapsed
#   --delete-source        delete the source PDF right after a successful conversion
#   --ingest               parallel chunk+embed+store (RAG) of converted md files
#   --ingest-only          ingest only (no conversion; pre-scans converted/)
#   --ingest-live-only     skip the pre-scan; only ingest files converted this run
#   --ingest-limit <n>     max files to ingest (0 = unlimited; testing aid)
#   --convert-limit <n>    max files to convert (0 = unlimited; testing aid)
MAX_RUN_MINUTES = None
DELETE_SOURCE = False
INGEST = False
INGEST_ONLY = False
INGEST_LIVE_ONLY = False
INGEST_LIMIT = 0
CONVERT_LIMIT = 0
_argv = sys.argv[1:]
if "--max-run-minutes" in _argv:
    MAX_RUN_MINUTES = int(_argv[_argv.index("--max-run-minutes") + 1])
if "--delete-source" in _argv:
    DELETE_SOURCE = True
if "--ingest" in _argv:
    INGEST = True
if "--ingest-only" in _argv:
    INGEST_ONLY = True
    INGEST = True  # ingest-only implies ingest
if "--ingest-live-only" in _argv:
    INGEST_LIVE_ONLY = True
    INGEST = True  # live-only implies ingest
if "--ingest-limit" in _argv:
    INGEST_LIMIT = int(_argv[_argv.index("--ingest-limit") + 1])
if "--convert-limit" in _argv:
    CONVERT_LIMIT = int(_argv[_argv.index("--convert-limit") + 1])

ARCHIVE = OUTPUT / "已入库"  # ingested md files are archived here (MAC phase rule removed)
INGEST_QUEUE = None  # queue.Queue of md Paths; set when --ingest

tracker = json.loads(TRACKER_PATH.read_text(encoding="utf-8"))

# Discover all PDFs/DOCX
files_to_convert = []
for ext in ["pdf", "docx"]:
    for f in sorted(SOURCE.rglob(f"*.{ext}")):
        files_to_convert.append(f)

print(f"Found {len(files_to_convert)} files to check")

# Filter
new_files = []
skipped_converted = []
skipped_failed = []
skipped_known = []
for f in files_to_convert:
    rel = str(f.relative_to(PROJECT).as_posix())
    if ((OUTPUT / f"{f.stem}.md").exists()
            or (ARCHIVE / f"{f.stem}.md").exists()):
        skipped_converted.append(f)
    elif rel in tracker["failed"]:
        skipped_failed.append(f)
    else:
        reason = next((r for p, r in SKIP_PREFIXES if f.stem.startswith(p)), None)
        if reason:
            skipped_known.append((f, reason))
        else:
            new_files.append(f)

print(f"Already converted (md exists): {len(skipped_converted)}")
for f in skipped_converted:
    print(f"  [skip] {f.name}")
print(f"Previously failed: {len(skipped_failed)}")
for f in skipped_failed:
    print(f"  [skip] {f.name}")
print(f"Skipped known duplicates/covered: {len(skipped_known)}")
for f, reason in skipped_known:
    print(f"  [skip-known] {f.name}  -- {reason}")
print(f"New to convert:   {len(new_files)}")

if not new_files and not INGEST_ONLY:
    print("Nothing to do.")
    sys.exit(0)

if "--dry-run" in sys.argv:
    print("\n[DRY-RUN] would convert these files:")
    for i, f in enumerate(new_files):
        size_mb = f.stat().st_size / 1048576
        mode = "extract" if size_mb > FLASH_LIMIT_MB else "flash"
        print(f"  [{i+1}] ({mode}) {f.name}")
    sys.exit(0)

TOKEN = os.environ.get("MINERU_TOKEN", "")


def run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          encoding="utf-8", cwd=str(PROJECT))


def extract_mode_cmd(f: Path) -> str:
    return (f'mineru-open-api extract "{f}" --format md --model pipeline '
            f'--language en --timeout {EXTRACT_TIMEOUT}')


def save_output(f: Path, result: subprocess.CompletedProcess) -> bool:
    """Write converted markdown to flat output. Returns True on success."""
    stem = f.stem
    output_file = OUTPUT / f"{stem}.md"
    # mineru-open-api v0.5.9 flash-extract writes output flat to
    # <output_dir>/<stem>.md directly (v0.5.x nested layout no longer used)
    if output_file.exists():
        return True
    # legacy flash-extract output layout: <output_dir>/<stem>/<stem>.md
    nested_candidates = [
        OUTPUT / stem / f"{stem}.md",
        OUTPUT / stem / "output.md",
        OUTPUT / stem / "0.md",
    ]
    for nested in nested_candidates:
        if nested.exists():
            content = nested.read_text(encoding="utf-8")
            output_file.write_text(content, encoding="utf-8")
            shutil.rmtree(OUTPUT / stem, ignore_errors=True)
            return True
    if result.stdout.strip():
        output_file.write_text(result.stdout, encoding="utf-8")
        return True
    return False


def record_success(f: Path, mode: str, result: subprocess.CompletedProcess) -> bool:
    rel = str(f.relative_to(PROJECT).as_posix())
    output_file = OUTPUT / f"{f.stem}.md"
    if output_file.exists() or result.stdout.strip():
        tracker["converted"][rel] = {
            "output": str(output_file.relative_to(PROJECT).as_posix()),
            "time": datetime.now().isoformat(),
            "mode": mode,
        }
        size_kb = output_file.stat().st_size / 1024 if output_file.exists() else 0
        print(f"  OK ({mode}) -> {output_file.name} ({size_kb:.1f} KB)")
        if INGEST_QUEUE is not None:
            INGEST_QUEUE.put(output_file)  # hand off for parallel RAG ingestion
        if DELETE_SOURCE and f.exists():
            f.unlink()
            print(f"  DELETED source: {f.name}")
        return True
    return False


def record_failure(f: Path, reason: str):
    rel = str(f.relative_to(PROJECT).as_posix())
    zotero_id = f.parent.name
    failed_dir = FAILED / zotero_id
    failed_dir.mkdir(parents=True, exist_ok=True)
    if f.exists():
        shutil.move(str(f), str(failed_dir / f.name))
    tracker["failed"][rel] = {"reason": reason[:500], "time": datetime.now().isoformat()}
    print(f"  -> Moved to flash-failed/{zotero_id}/  ({reason[:120]})")


class Ingestor(threading.Thread):
    """Parallel RAG ingestion worker: chunk + embed + store + archive.

    Consumes a queue of md Paths (fed by the conversion loop and/or the
    pre-scan of converted/). Reuses index/build_index.py logic and params
    (heading-aware chunking, bge-m3, Chroma "papers" collection, sha256
    manifest). A single worker thread keeps Chroma/manifest access serial.
    Ingestion failure never blocks conversion (logged and skipped).
    """

    def __init__(self, work_queue, limit=0, live_only=False):
        super().__init__(daemon=True, name="ingestor")
        self.work_queue = work_queue
        self.limit = limit
        self.live_only = live_only
        self.enqueued = 0
        self.processed = 0
        self.ingested = 0
        self.failed = 0
        self.skipped = 0
        self.moved = 0
        self.prescan_done = threading.Event()  # set after pre-scan finishes enqueueing
        self.startup_error = None

    def run(self):
        try:
            import torch
            sys.path.insert(0, str(PROJECT / "index"))
            import build_index as bi

            torch.set_num_threads(max(1, os.cpu_count() or 8))
            ARCHIVE.mkdir(parents=True, exist_ok=True)
            manifest = {"version": 1, "model": bi.MODEL_NAME, "files": {}}
            if bi.MANIFEST_PATH.exists():
                manifest = json.loads(bi.MANIFEST_PATH.read_text(encoding="utf-8"))

            device = "cuda" if torch.cuda.is_available() else "cpu"
            model_path = str(bi.LOCAL_MODEL_DIR) if bi.LOCAL_MODEL_DIR.exists() else bi.MODEL_NAME
            print(f"[INGEST] loading {model_path} (device={device}) ...", flush=True)
            model = bi.SentenceTransformer(model_path, device=device)
            if device == "cuda":
                model.half()  # fp16 to fit the 2 GB laptop GPU
                torch.cuda.empty_cache()
            tokenizer = model[0].tokenizer
            client = bi.chromadb.PersistentClient(
                path=str(bi.CHROMA_DIR),
                settings=bi.ChromaSettings(anonymized_telemetry=False),
            )
            collection = client.get_or_create_collection(
                "papers", metadata={"hnsw:space": "cosine"}
            )
            by_stem, by_folder, by_doi = bi.load_meta()
            print("[INGEST] ready", flush=True)

            if not self.live_only:
                for _, md in bi.markdown_files():
                    # Archived files are already represented by the manifest;
                    # only top-level files are eligible for a move into archive.
                    if md.parent != OUTPUT:
                        continue
                    if self.limit and self.enqueued >= self.limit:
                        break
                    rel = md.name
                    h = hashlib.sha256(md.read_bytes()).hexdigest()
                    if manifest["files"].get(rel, {}).get("sha256") == h:
                        continue
                    self.work_queue.put(md)
                    self.enqueued += 1

            self.prescan_done.set()  # sentinel may only be enqueued after this

            while True:
                item = self.work_queue.get()
                if item is None:
                    break
                self._ingest_one(item, bi, collection, model, tokenizer, manifest,
                                 by_stem, by_folder, by_doi)

            manifest["built_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            manifest["total_chunks"] = sum(
                f.get("chunks", 0) for f in manifest["files"].values()
            )
            bi.write_manifest_atomic(manifest)
            print(f"[INGEST] finished: ingested={self.ingested} moved={self.moved} "
                  f"skipped={self.skipped} failed={self.failed}", flush=True)
        except Exception as e:  # noqa: BLE001 - never kill the conversion thread
            self.startup_error = str(e)
            self.prescan_done.set()
            print(f"[INGEST] FATAL: {e}", flush=True)

    def _ingest_one(self, md, bi, collection, model, tokenizer, manifest,
                    by_stem, by_folder, by_doi):
        self.processed += 1
        if self.limit and self.processed > self.limit:
            print(f"[INGEST] limit reached, skipping {md.name}", flush=True)
            return
        rel = md.name
        if not md.exists():  # duplicate enqueue: file already archived -> no-op
            self.skipped += 1
            return
        try:
            h = hashlib.sha256(md.read_bytes()).hexdigest()
            if manifest["files"].get(rel, {}).get("sha256") == h:
                self.skipped += 1
                return
            meta, is_alias = bi.meta_for(rel, by_stem, by_folder, by_doi)
            text = md.read_text(encoding="utf-8", errors="replace")
            chunks = bi.chunk_markdown(text, tokenizer)
            if not chunks:
                bi.replace_source(collection, rel, [], [], [], None)
                manifest["files"][rel] = {"sha256": h, "chunks": 0, "mac": meta["mac"]}
                self.skipped += 1
                print(f"[INGEST] SKIP (no text): {rel}", flush=True)
                return
            docs = [c["text"] for c in chunks]
            sections = [c["section"] for c in chunks]
            ids = [f"{h[:12]}-{i:03d}" for i in range(len(chunks))]
            metas = []
            for i, c in enumerate(chunks):
                m = dict(meta)
                m.update({"source": rel, "section": sections[i], "chunk": i,
                          "supp": "yes" if is_alias else "no"})
                metas.append(m)
            batch = 16 if str(model.device).startswith("cuda") else 48
            embeddings = model.encode(docs, batch_size=batch,
                                      normalize_embeddings=True, show_progress_bar=False)
            bi.replace_source(collection, rel, ids, docs, metas, embeddings)
            manifest["files"][rel] = {"sha256": h, "chunks": len(chunks), "mac": meta["mac"]}
            self.ingested += 1
            tag = f"[{meta['mac']}]" if meta["mac"] != "unknown" else "[?]"
            print(f"[INGEST] {tag} {rel} -> {len(chunks)} chunks", flush=True)
            dest = ARCHIVE / rel  # archive all ingested md (uniform pipeline)
            if dest.exists():
                dest.unlink()
            shutil.move(str(md), str(dest))
            self.moved += 1
            print(f"[INGEST] archived -> converted/已入库/{rel}", flush=True)
        except Exception as e:  # noqa: BLE001
            self.failed += 1
            print(f"[INGEST] FAIL {rel}: {str(e)[:300]}", flush=True)


# Start the parallel ingestor when requested (before any conversion work).
if INGEST:
    INGEST_QUEUE = queue.Queue()
    ingestor = Ingestor(INGEST_QUEUE, limit=INGEST_LIMIT, live_only=INGEST_LIVE_ONLY)
    ingestor.start()
    if INGEST_ONLY:
        ingestor.prescan_done.wait()  # sentinel must trail pre-scan items
        INGEST_QUEUE.put(None)
        ingestor.join()
        print(f"[INGEST-ONLY] done: ingested={ingestor.ingested} moved={ingestor.moved} "
              f"skipped={ingestor.skipped} failed={ingestor.failed}")
        sys.exit(0)


# Convert
success_count = 0
fail_count = 0
T_START = time.monotonic()
for i, f in enumerate(new_files):
    if CONVERT_LIMIT and i >= CONVERT_LIMIT:
        print(f"\nConvert limit reached ({CONVERT_LIMIT}); stopping.")
        break
    if MAX_RUN_MINUTES and i > 0 and (time.monotonic() - T_START) >= MAX_RUN_MINUTES * 60:
        print(f"\nTime cap reached ({MAX_RUN_MINUTES} min elapsed); "
              "stopping before next file (current file finished).")
        break
    size_mb = f.stat().st_size / 1048576
    print(f"\n[{i+1}/{len(new_files)}] {f.name} ({size_mb:.1f} MB)")
    print(f"  Source: papers/{f.parent.name}/")

    # Decide mode: flash for small files, extract for big ones
    use_extract = size_mb > FLASH_LIMIT_MB
    if use_extract and not TOKEN:
        record_failure(f, f"file {size_mb:.1f}MB > {FLASH_LIMIT_MB:.0f}MB flash limit; "
                          "extract mode requires MINERU_TOKEN env")
        fail_count += 1
        tracker["last_run"] = datetime.now().isoformat()
        TRACKER_PATH.write_text(json.dumps(tracker, indent=2, ensure_ascii=False), encoding="utf-8")
        continue

    if use_extract:
        print("  Mode: extract (large file)")
        result = run(extract_mode_cmd(f))
        if result.returncode == 0 and save_output(f, result) and record_success(f, "extract", result):
            success_count += 1
        else:
            print(f"  FAILED (exit {result.returncode})")
            print(f"  stderr: {result.stderr[:300]}")
            record_failure(f, result.stderr[:500] if result.stderr else "extract failed")
            fail_count += 1
    else:
        cmd = f'mineru-open-api flash-extract "{f}" -o "{OUTPUT}" --language en'
        result = run(cmd)
        if result.returncode == 0 and save_output(f, result) and record_success(f, "flash-extract", result):
            success_count += 1
        else:
            print(f"  flash FAILED (exit {result.returncode}); retrying with extract ...")
            print(f"  stderr: {result.stderr[:200]}")
            if not TOKEN:
                print("  (no MINERU_TOKEN — cannot retry with extract)")
                record_failure(f, result.stderr[:500] if result.stderr else "flash failed, no token for extract retry")
                fail_count += 1
            else:
                result2 = run(extract_mode_cmd(f))
                if result2.returncode == 0 and save_output(f, result2) and record_success(f, "extract-retry", result2):
                    success_count += 1
                else:
                    print(f"  extract retry FAILED (exit {result2.returncode})")
                    print(f"  stderr: {result2.stderr[:300]}")
                    record_failure(f, result2.stderr[:500] if result2.stderr else "extract retry failed")
                    fail_count += 1

    tracker["last_run"] = datetime.now().isoformat()
    TRACKER_PATH.write_text(json.dumps(tracker, indent=2, ensure_ascii=False), encoding="utf-8")

if INGEST:
    # The pre-scan runs after model loading.  Keep the sentinel behind it even
    # in the ordinary conversion+ingestion flow.
    ingestor.prescan_done.wait()
    if ingestor.startup_error:
        print(f"[INGEST] ERROR: {ingestor.startup_error}")
    INGEST_QUEUE.put(None)
    ingestor.join()

print(f"\n{'='*50}")
print(f"Done! Success: {success_count}, Failed: {fail_count}, "
      f"Skipped: {len(skipped_converted) + len(skipped_failed) + len(skipped_known)}")
if fail_count > 0:
    print(f"Failed files moved to: flash-failed/")
