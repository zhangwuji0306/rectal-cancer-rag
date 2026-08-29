#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan PDFs under papers/ for page counts + sizes.

Estimation aid for batch conversion: flash-extract is limited to
<=10 MB and <=20 pages; anything beyond that falls back to extract mode
(roughly 2x cost). Writes .claude/tmp/page_scan.json and prints a summary.

Usage: PYTHONIOENCODING=utf-8 .venv/Scripts/python .claude/tmp/scan_pages.py
"""
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from pypdf import PdfReader

PROJECT = Path("E:/writing-rag")
OUT = PROJECT / ".claude" / "tmp" / "page_scan.json"

results = []
for f in sorted((PROJECT / "papers").glob("*.pdf")):
    try:
        with f.open("rb") as fh:
            pages = len(PdfReader(fh).pages)
        err = ""
    except Exception as e:  # noqa: BLE001 - broken PDF must not kill the scan
        pages = -1
        err = str(e)[:200]
    size_mb = round(f.stat().st_size / 1048576, 2)
    results.append({"file": f.name, "size_mb": size_mb, "pages": pages, "err": err})

OUT.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")

n_flash_ok = sum(1 for r in results if 0 <= r["pages"] <= 20 and r["size_mb"] <= 10)
n_big = sum(1 for r in results if r["size_mb"] > 10)
n_long = sum(1 for r in results if r["pages"] > 20)
n_both = sum(1 for r in results if r["pages"] > 20 and r["size_mb"] > 10)
n_bad = sum(1 for r in results if r["pages"] == -1)
print(f"Total PDFs: {len(results)}")
print(f"  flash-eligible (<=10MB and <=20 pages): {n_flash_ok}")
print(f"  >10MB (extract required): {n_big}")
print(f"  >20 pages (flash will fail -> extract): {n_long}")
print(f"  both (extract regardless): {n_both}")
print(f"  unreadable (pages=-1): {n_bad}")
print(f"Saved: {OUT}")
