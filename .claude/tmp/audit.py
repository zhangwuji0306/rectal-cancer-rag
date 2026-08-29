# -*- coding: utf-8 -*-
import json, pathlib, sys
sys.stdout.reconfigure(encoding="utf-8")
data = json.loads(pathlib.Path(r"E:\writing-rag\.claude\tmp\mapping.json").read_text(encoding="utf-8"))
files = data["files"]

print("=" * 100)
print("NON-MAC FILES (9):")
for f in files:
    if not f["mac"]:
        print(f"  - {f['filename']}")
        print(f"      folder={f['folder']} csv_key={f['csv_key']} doi={f['doi']}")
        print(f"      title={f['title']}")

print("=" * 100)
print("MAC by title-judgement only (not matched to user's reference lists):")
for f in files:
    if f["mac"] and f["evidence"].startswith("标题"):
        print(f"  - {f['filename'][:90]}")
        print(f"      csv_key={f['csv_key']} doi={f['doi']} year={f['year']}")

print("=" * 100)
print("REFERENCED IN DOCS BUT NO PDF IN papers/ (10):")
for m in data["missing_refs"]:
    print(f"  - [{m['key']}] {m['title']} ({m['year']}) doi={m['doi']}")
    print(f"      ref: {m['ref'][:120]}")

print("=" * 100)
print("REFERENCES (61) NOT FOUND AMONG 81 PDFs:")
present_dois = {f["doi"] for f in files if f["doi"]}
present_titles = {f["title"] for f in files if f["title"]}
for e in data["refs"]:
    doi = (e["doi"] or "").rstrip(".").lower()
    if doi and doi in {d.rstrip(".").lower() for d in present_dois if d}:
        continue
    # check title-based presence via files
    hit = any((f["title"] or "").lower() in (e["raw"] or "").lower() or (e["raw"] or "").lower()[:60] in (f["title"] or "").lower() for f in files)
    if not hit:
        print(f"  - doi={e['doi']} | {e['raw'][:130]}")
