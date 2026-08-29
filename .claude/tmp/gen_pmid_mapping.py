#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate .claude/tmp/mapping_pmid.json from 直肠癌文献爬取/索引信息.csv.

Metadata source for the general rectal-cancer imaging batch (PMID_*.pdf).
Stems are collected from: converted/*.md + converted/已入库/*.md + papers/*.pdf
(so already-converted files whose source PDF was deleted still get metadata).

Schema mirrors mapping.json ("files" list) — consumed by index/build_index.py
load_meta() and the new parallel-ingest path in .claude/batch_convert.py.

mac rule (AGENTS.md hard rule): title contains mucin/mucus/黏液 -> mac=true.

Usage: PYTHONIOENCODING=utf-8 .venv/Scripts/python .claude/tmp/gen_pmid_mapping.py
"""
import csv
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path("E:/writing-rag")
CRAWLER_ROOT = ROOT / "直肠癌文献爬取"
CSV_PATH = CRAWLER_ROOT / "索引信息.csv"
OUT_PATH = ROOT / ".claude" / "tmp" / "mapping_pmid.json"
ARCHIVE = ROOT / "converted" / "已入库"

# Collect PMID stems from every known location (converted, archived, pending).
stems = set()
for p in list((ROOT / "converted").glob("*.md")) + list(ARCHIVE.glob("*.md")):
    m = re.match(r"^PMID_(\d+)$", p.stem)
    if m:
        stems.add(m.group(1))
for p in (ROOT / "papers").glob("*.pdf"):
    m = re.match(r"^PMID_(\d+)\.pdf$", p.name)
    if m:
        stems.add(m.group(1))

rows = {}
with CSV_PATH.open(encoding="utf-8-sig", newline="") as fh:  # BOM-safe
    for raw in csv.DictReader(fh):
        # Accept files carrying either one or an accidental duplicate BOM.
        r = {
            (k.lstrip("\ufeff") if isinstance(k, str) else k): v
            for k, v in raw.items()
        }
        pmid = (r.get("PMID") or "").strip()
        if pmid:
            rows[pmid] = r

files = []
unmatched = []
mac_hits = []
for pmid in sorted(stems, key=int):
    row = rows.get(pmid)
    if row is None:
        unmatched.append(pmid)
        continue
    title = (row.get("Title") or "").strip()
    is_mac = bool(re.search(r"mucin|mucus|黏液", title, re.IGNORECASE))
    pdf = ROOT / "papers" / f"PMID_{pmid}.pdf"
    md = ROOT / "converted" / f"PMID_{pmid}.md"
    arch_md = ARCHIVE / f"PMID_{pmid}.md"
    if is_mac:
        mac_hits.append((f"PMID_{pmid}", title[:80]))
    files.append({
        "filename": f"PMID_{pmid}.pdf",
        "size_mb": round(pdf.stat().st_size / 1048576, 2) if pdf.exists() else 0,
        "converted": md.exists() or arch_md.exists(),
        "mac": is_mac,
        "evidence": "索引信息.csv",
        "csv_key": pmid,
        "title": title,
        "year": (row.get("Year") or "").strip(),
        "journal": (row.get("Journal") or "").strip(),
        "doi": (row.get("DOI") or "").strip(),
        "author": (row.get("FirstAuthor") or "").strip(),
    })

OUT_PATH.write_text(json.dumps({"files": files}, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"entries: {len(files)}  (stems found: {len(stems)})")
print(f"unmatched by CSV: {len(unmatched)}")
for u in unmatched:
    print(f"  NO CSV ROW: PMID_{u}")
print(f"MAC by title rule: {len(mac_hits)}")
for name, t in mac_hits:
    print(f"  {name} | {t}")
print(f"Saved: {OUT_PATH}")
