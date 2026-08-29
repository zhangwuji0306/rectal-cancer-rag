# -*- coding: utf-8 -*-
"""Copy converted markdown of MAC-related papers into mucinous/ (idempotent)."""
import json, pathlib, sys, shutil

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path("E:/writing-rag")
data = json.loads((ROOT / ".claude/tmp/mapping.json").read_text(encoding="utf-8"))
CONVERTED = ROOT / "converted"
MUCINOUS = ROOT / "mucinous"
MUCINOUS.mkdir(exist_ok=True)

copied, skipped_missing, nonmac = 0, 0, 0
for f in data["files"]:
    stem = f["filename"].rsplit(".", 1)[0]
    md = CONVERTED / f"{stem}.md"
    if not f["mac"]:
        nonmac += 1
        continue
    if not md.exists():
        skipped_missing += 1
        continue
    dest = MUCINOUS / md.name
    if dest.exists() and dest.stat().st_size == md.stat().st_size:
        continue  # already synced
    shutil.copy2(md, dest)
    copied += 1
    print(f"  + {md.name}")

print(f"[SYNC] copied={copied} already-synced/dup=skipped, nonmac={nonmac}, mac-but-md-missing={skipped_missing}")
print(f"[SYNC] mucinous/ now has {len(list(MUCINOUS.glob('*.md')))} files")
