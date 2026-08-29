# -*- coding: utf-8 -*-
"""Build mapping: CSV index <-> papers/ PDFs <-> converted/ ; extract doc references; classify MAC."""
import csv, json, pathlib, re, sys, unicodedata

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path("E:/writing-rag")
CSV_PATH = ROOT / "papers/索引信息.csv"
DOCS_DIR = ROOT / ".claude/tmp/mac_docs"
OUT_JSON = ROOT / ".claude/tmp/mapping.json"

BS_PAT = re.escape("\\")  # literal backslash pattern

# ---------------- 1. Parse CSV ----------------
rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
csv_by_storage = {}  # storage_key -> row
for r in rows:
    fa = r.get("File Attachments") or ""
    keys = re.findall(r"storage" + BS_PAT + r"([A-Za-z0-9]{8})", fa)
    for k in keys:
        csv_by_storage.setdefault(k, r)  # keep first
print(f"[CSV] rows={len(rows)}, storage-keyed rows={len(csv_by_storage)}")

# ---------------- 2. Papers PDFs ----------------
pdfs = sorted(
    p for ext in ("pdf", "docx") for p in (ROOT / "papers").rglob(f"*.{ext}")
    if p.parent.name != "papers"
)
print(f"[PAPERS] pdf/docx files={len(pdfs)}")

converted_stems = {p.stem for p in (ROOT / "converted").glob("*.md")}
print(f"[CONVERTED] md files={len(converted_stems)}")

# ---------------- 3. Extract references from user docs ----------------
ref_pat = re.compile(r"^\s*(\d+)[\.\t]\s*(.+)$")

def extract_refs(txt: str):
    """Return list of raw reference strings from the LAST consecutive numbered block (1..N)."""
    lines = txt.splitlines()
    blocks = []  # list of list[(num, text)]
    cur = []
    for ln in lines:
        m = ref_pat.match(ln)
        if m:
            num = int(m.group(1))
            if cur and num == cur[-1][0] + 1:
                cur.append((num, m.group(2)))
            else:
                if cur:
                    blocks.append(cur)
                cur = [(num, m.group(2))]
        else:
            if cur:
                blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    # keep the last block that starts at 1 and has >= 3 items
    for b in reversed(blocks):
        if b[0][0] == 1 and len(b) >= 3:
            return [t for _, t in b]
    return []

refs = []
for f in sorted(DOCS_DIR.glob("*.txt")):
    raw = extract_refs(f.read_text(encoding="utf-8"))
    print(f"[DOCS] {f.stem}: {len(raw)} refs")
    refs.append({"doc": f.stem, "raw": raw})

def norm_doi(s):
    """Extract DOI token from raw text, pure values, or URLs."""
    if not s:
        return None
    m = re.search(r"(10\.\d{4,9}/[^\s,;]+)", (s or "").lower())
    return m.group(1).rstrip(".") if m else None

def first_author_year(s):
    m = re.search(r"^([^,]+),\s+[^0-9()]*?(\d{4})", s)
    if m:
        fam = m.group(1).split()[-1].lower().strip(" .")
        return fam, m.group(2)
    return None, None

# dedupe refs by doi or (first author family, year)
uniq = {}
for doc in refs:
    for raw in doc["raw"]:
        doi = norm_doi(raw)
        fam, yr = first_author_year(raw)
        key = doi or (fam, yr)
        if key and key not in uniq:
            uniq[key] = {"raw": raw, "doi": doi, "first_author": fam, "year": yr, "docs": []}
        if key:
            uniq[key]["docs"].append(doc["doc"])
print(f"[REFS] unique references = {len(uniq)}")

# ---------------- 4. Match CSV rows to references ----------------
def title_norm(s):
    s = (s or "").lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s).strip()

ref_entries = list(uniq.values())
def match_ref(row):
    doi = norm_doi(row.get("DOI"))
    if doi:
        for e in ref_entries:
            if e["doi"] and e["doi"].rstrip(".") == doi.rstrip("."):
                return e
    # title overlap
    t = title_norm(row.get("Title"))
    if len(t) < 8:
        return None
    tw = set(t.split())
    best, best_score = None, 0.0
    for e in ref_entries:
        eraw = title_norm(e["raw"])
        ew = set(eraw.split())
        inter = len(tw & ew)
        if inter == 0:
            continue
        score = inter / max(len(tw), len(ew))
        if score > best_score:
            best, best_score = e, score
    return best if best_score >= 0.45 else None

for r in rows:
    r["_ref"] = match_ref(r)

# ---------------- 5. Classify by title ----------------
MAC_WORDS = ["mucin", "mucus", "mucous", "黏液"]
def title_is_mac(title):
    t = (title or "").lower()
    return any(w in t for w in MAC_WORDS)

# ---------------- 6. Build per-file record ----------------
files = []
for p in pdfs:
    folder = p.parent.name
    row = csv_by_storage.get(folder)
    stem = p.stem
    converted = stem in converted_stems
    if row:
        ref = row.get("_ref")
        in_refs = ref is not None
        mac_by_title = title_is_mac(row.get("Title"))
        if in_refs:
            mac, evidence = True, "参考文献清单"
        elif mac_by_title:
            mac, evidence = True, "标题判断(含mucin/mucus/黏液)"
        else:
            mac, evidence = False, "标题判断(非黏液腺癌)"
        meta = {
            "csv_key": row.get("Key"), "title": row.get("Title"), "year": row.get("Publication Year"),
            "journal": row.get("Publication Title"), "doi": row.get("DOI"),
            "author": row.get("Author"),
            "abstract": (row.get("Abstract Note") or "")[:200],
        }
    else:
        ref = None; in_refs = False
        mac_by_title = title_is_mac(p.stem)
        if mac_by_title:
            mac, evidence = True, "标题判断(含mucin/mucus/黏液)"
        else:
            mac, evidence = False, "标题判断(非黏液腺癌)"
        meta = {"csv_key": None, "title": p.stem, "year": None, "journal": None, "doi": None, "abstract": ""}
    files.append({
        "folder": folder, "filename": p.name, "size_mb": round(p.stat().st_size / 1048576, 2),
        "converted": converted, "mac": mac, "evidence": evidence,
        "in_user_refs": in_refs, "ref_raw": (ref or {}).get("raw", ""),
        **meta,
    })

mac_n = sum(1 for f in files if f["mac"])
conv_n = sum(1 for f in files if f["converted"])
print(f"[CLASSIFY] total={len(files)} MAC={mac_n} non-MAC={len(files)-mac_n} already-converted={conv_n}")

# ---------------- 7. Sanity: referenced but folder missing ----------------
ref_csv_keys = {r["Key"] for r in rows if r.get("_ref")}
present = {f["csv_key"] for f in files if f["csv_key"]}
missing_refs = [
    {"key": r["Key"], "title": r["Title"], "year": r["Publication Year"],
     "doi": r.get("DOI"), "ref": (r.get("_ref") or {}).get("raw", "")}
    for r in rows if r.get("_ref") and r["Key"] not in present
]
print(f"[MISSING] referenced-in-docs but no PDF in papers/: {len(missing_refs)}")

data = {
    "files": files,
    "refs": [{"doi": e["doi"], "first_author": e["first_author"], "year": e["year"],
              "raw": e["raw"], "docs": e["docs"]} for e in ref_entries],
    "missing_refs": missing_refs,
}
OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"[SAVED] {OUT_JSON}")

# quick print of classification
for f in files:
    print(("MAC " if f["mac"] else "non") + f" | {f['evidence']:14s} | conv={int(f['converted'])} | {f['filename'][:80]}")
