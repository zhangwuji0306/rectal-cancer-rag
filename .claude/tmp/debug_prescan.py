# -*- coding: utf-8 -*-
"""Debug: replicate Ingestor pre-scan decision logic."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, r"E:\writing-rag\index")
OUTPUT = Path(r"E:\writing-rag\converted")
m = json.loads(Path(r"E:\writing-rag\index\manifest.json").read_text(encoding="utf-8"))
print("manifest files type:", type(m["files"]).__name__)
print("manifest files keys sample:", list(m["files"].keys())[:3])
mdlist = sorted(OUTPUT.glob("*.md"))
print("top-level md count:", len(mdlist))
enq = 0
for md in mdlist[:5]:
    rel = md.name
    h = hashlib.sha256(md.read_bytes()).hexdigest()
    hit = m["files"].get(rel, {}).get("sha256") == h
    print(f"  {rel}  sha_match={hit}")
    if not hit:
        enq += 1
print("would enqueue (first 5):", enq)
