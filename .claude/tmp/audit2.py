# -*- coding: utf-8 -*-
import json, pathlib, hashlib, sys
sys.stdout.reconfigure(encoding="utf-8")
data = json.loads(pathlib.Path(r"E:\writing-rag\.claude\tmp\mapping.json").read_text(encoding="utf-8"))
files = data["files"]

print("### FULL TITLES of title-judged MAC cases:")
for f in files:
    if f["mac"] and f["evidence"].startswith("标题"):
        print(f"  folder={f['folder']} | {f['title']} | {f['year']} | doi={f['doi']}")

print()
print("### DUPLICATES (same doi or same normalized title):")
from collections import defaultdict
by_doi = defaultdict(list)
for f in files:
    key = f["doi"] or ("NOTITLE:" + f["filename"][:40])
    by_doi[key].append(f)
for k, lst in by_doi.items():
    if len(lst) > 1:
        print(f"  DOI {k}:")
        for f in lst:
            md5 = hashlib.md5(pathlib.Path(rf"E:\writing-rag\papers\{f['folder']}\{f['filename']}").read_bytes()).hexdigest()[:10]
            print(f"    folder={f['folder']} size={f['size_mb']}MB md5={md5} conv={int(f['converted'])}")
            print(f"      {f['filename'][:100]}")
