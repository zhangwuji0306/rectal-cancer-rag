# -*- coding: utf-8 -*-
"""T2 verification: retrieve hits + metadata from CSV mapping."""
import sys

sys.path.insert(0, r"E:\writing-rag\index")
from rag_core import retrieve

queries = [
    "preoperative radiochemotherapy long-term results phase II trial rectal cancer",
    "tumor regression grade chemoradiotherapy prediction",
]
for q in queries:
    print(f"=== Q: {q}")
    for h in retrieve(q, k=3):
        print(f"  #{h['rank']} score={h['score']} src={h['source']} mac={h['mac']}")
        print(f"     title: {h['title'][:80]}")
        print(f"     meta : {h['journal']} | {h['year']} | doi={h['doi']} | author={h['authors'][:30]}")
