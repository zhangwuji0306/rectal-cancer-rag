# -*- coding: utf-8 -*-
"""Final verification: mucinous/ contains exactly the MAC papers; non-MAC excluded."""
import json, pathlib, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path("E:/writing-rag")
data = json.loads((ROOT / ".claude/tmp/mapping.json").read_text(encoding="utf-8"))
files = data["files"]

muc = {p.name for p in (ROOT / "mucinous").glob("*.md")}
conv = {p.name for p in (ROOT / "converted").glob("*.md")}
mac_stems = {f["filename"].rsplit(".", 1)[0] for f in files if f["mac"]}
nonmac_stems = {f["filename"].rsplit(".", 1)[0] for f in files if not f["mac"]}

# 1) files in mucinous that are NOT in mac list (violations)
violations = []
for name in muc:
    stem = name.rsplit(".", 1)[0]
    if stem not in mac_stems:
        violations.append(name)
print(f"[CHECK1] mucinous files: {len(muc)}; not-in-MAC-list: {len(violations)}")
for v in violations:
    print("  VIOLATION:", v)

# 2) mac papers whose md is missing from mucinous
missing = []
for f in files:
    if f["mac"]:
        stem = f["filename"].rsplit(".", 1)[0]
        if f"{stem}.md" not in muc:
            missing.append((f["filename"], "no converted md" if f"{stem}.md" not in conv else "not in mucinous"))
print(f"[CHECK2] MAC papers missing from mucinous: {len(missing)}")
for m in missing:
    print("  MISSING:", m)

# 3) non-mac papers present in mucinous
nonmac_in_muc = [n for n in muc if n.rsplit(".", 1)[0] in nonmac_stems]
print(f"[CHECK3] non-MAC files wrongly in mucinous: {len(nonmac_in_muc)}")

# 4) summary counts
print("\n=== FINAL SUMMARY ===")
print(f"papers/: {len(files)} pdf/docx")
print(f"  MAC: {sum(1 for f in files if f['mac'])} (evidence: 参考文献 {sum(1 for f in files if f['in_user_refs'])} + 标题判断 {sum(1 for f in files if f['mac'] and not f['in_user_refs'])})")
print(f"  non-MAC: {sum(1 for f in files if not f['mac'])}")
print(f"converted/: {len(conv)} md files")
print(f"mucinous/: {len(muc)} md files (MAC corpus)")
print(f"skip-pending: {sum(1 for f in files if not f['conv_now'])} (ESGARx2 + Melis duplicate)")
print(f"refs in user docs: {len(data['refs'])} unique; missing PDF in library: {len(data['missing_refs'])}")
