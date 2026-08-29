#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark converter: convert a few sample PDFs with the exact commands
batch_convert.py uses, measure wall time per file, and register successes in
converted/ + conversion-tracker.json so the full run skips them.

Usage:
  PYTHONIOENCODING=utf-8 .venv/Scripts/python .claude/tmp/bench_convert.py <pdf1> <pdf2> ...
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT = Path("E:/writing-rag")
OUTPUT = PROJECT / "converted"
TRACKER_PATH = PROJECT / ".claude" / "conversion-tracker.json"
EXTRACT_TIMEOUT = "7200"


def run(cmd):
    t0 = time.monotonic()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", cwd=str(PROJECT))
    return r, time.monotonic() - t0


def save_output(f, stdout):
    """Mirror batch_convert.save_output: flatten nested output to converted/<stem>.md."""
    stem = f.stem
    output_file = OUTPUT / f"{stem}.md"
    if output_file.exists():
        return True
    for nested in (OUTPUT / stem / f"{stem}.md",
                   OUTPUT / stem / "output.md",
                   OUTPUT / stem / "0.md"):
        if nested.exists():
            output_file.write_text(nested.read_text(encoding="utf-8"), encoding="utf-8")
            shutil.rmtree(OUTPUT / stem, ignore_errors=True)
            return True
    if stdout.strip():
        output_file.write_text(stdout, encoding="utf-8")
        return True
    return False


def record(f, mode):
    """Mirror batch_convert.record_success: register in tracker (idempotent for full run)."""
    rel = str(f.relative_to(PROJECT).as_posix())
    tracker = json.loads(TRACKER_PATH.read_text(encoding="utf-8"))
    tracker["converted"][rel] = {
        "output": f"converted/{f.stem}.md",
        "time": datetime.now().isoformat(),
        "mode": mode,
    }
    tracker["last_run"] = datetime.now().isoformat()
    TRACKER_PATH.write_text(json.dumps(tracker, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv):
    token = os.environ.get("MINERU_TOKEN", "")
    summary = []
    for p in argv:
        f = Path(p)
        size_mb = f.stat().st_size / 1048576
        use_extract = size_mb > 10.0
        entry = {"file": f.name, "size_mb": round(size_mb, 2),
                 "mode": None, "ok": False, "elapsed": None, "detail": ""}
        t_total = time.monotonic()
        if use_extract:
            if not token:
                entry["detail"] = "no MINERU_TOKEN for extract"
            else:
                r, dt = run(f'mineru-open-api extract "{f}" --format md --model pipeline '
                            f'--language en --timeout {EXTRACT_TIMEOUT}')
                if r.returncode == 0 and save_output(f, r.stdout):
                    record(f, "extract")
                    entry.update(mode="extract", ok=True)
                else:
                    entry["detail"] = (r.stderr or "")[:200]
                    entry["mode"] = "extract"
        else:
            r, dt_flash = run(f'mineru-open-api flash-extract "{f}" -o "{OUTPUT}" --language en')
            if r.returncode == 0 and save_output(f, r.stdout):
                record(f, "flash-extract")
                entry.update(mode="flash-extract", ok=True)
            elif token:
                entry["flash_elapsed"] = round(dt_flash, 1)
                r2, dt_ext = run(f'mineru-open-api extract "{f}" --format md --model pipeline '
                                 f'--language en --timeout {EXTRACT_TIMEOUT}')
                if r2.returncode == 0 and save_output(f, r2.stdout):
                    record(f, "extract-retry")
                    entry.update(mode="extract-retry", ok=True)
                else:
                    entry.update(mode="extract-retry",
                                 detail=(r.stderr or r2.stderr or "")[:200])
            else:
                entry.update(mode="flash-extract", detail=(r.stderr or "")[:200])
        entry["elapsed"] = round(time.monotonic() - t_total, 1)
        summary.append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    print("BENCH_DONE")


if __name__ == "__main__":
    main(sys.argv[1:])
