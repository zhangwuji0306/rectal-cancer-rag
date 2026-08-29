"""Smoke-test chunking without model weights (tokenizer only)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transformers import AutoTokenizer

from build_index import chunk_markdown

TOK_DIR = Path(r"E:\writing-rag\.model-cache\BAAI__bge-m3")
tok = AutoTokenizer.from_pretrained(str(TOK_DIR))

files = [
    "Cao 等 - 2020 - A New MRI-Defined Biomarker for Rectal Mucinous Adenocarcinoma Mucin Pool Patterns in Determining t.md",
    "ESGAR202601 PARTI.md",
    "zrac039_supplementary_data.md",
]
files += [p.name for p in (Path(r"E:\writing-rag\converted")).glob("Hendrick*")]
total = 0
for rel in files:
    text = (Path(r"E:\writing-rag\converted") / rel).read_text(encoding="utf-8", errors="replace")
    chunks = chunk_markdown(text, tok)
    sizes = [len(tok.encode(c["text"], add_special_tokens=False)) for c in chunks]
    total += len(chunks)
    print(f"{rel[:55]:<57} chunks={len(chunks):>3}  tokens min={min(sizes):>4} max={max(sizes):>4}")
    for c in chunks[:3]:
        print(f"    section={c['section'] or '-'}  text={c['text'][:60]!r}")
print("TOTAL chunks:", total)
