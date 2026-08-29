"""Query the built Chroma index (thin CLI over rag_core).

Usage:
    python index/retrieve.py "问题或英文查询" [-k 8] [--mac-only] [--json]
    python index/retrieve.py "query" --db <chroma_db_dir> --model <model_dir> --device cuda

Any other project can reuse the same index programmatically:
    import sys; sys.path.insert(0, r"E:\\writing-rag\\index")
    from rag_core import retrieve, dedupe_by_doi, summary_list
"""

import argparse
import json
import sys

from rag_core import DEFAULT_DB_DIR, DEFAULT_MODEL_DIR, retrieve

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="retrieval query (Chinese or English)")
    ap.add_argument("-k", type=int, default=8, help="top-k results (default 8)")
    ap.add_argument("--mac-only", action="store_true", help="only MAC-classified chunks")
    ap.add_argument("--json", action="store_true", help="dump raw JSON")
    ap.add_argument("--db", default=str(DEFAULT_DB_DIR), help="chroma_db directory")
    ap.add_argument("--model", default=str(DEFAULT_MODEL_DIR), help="local model directory")
    ap.add_argument("--device", default="cpu", help="torch device (cpu|cuda|auto)")
    args = ap.parse_args()

    device = args.device
    if device == "auto":
        import torch  # noqa: PLC0415

        device = "cuda" if torch.cuda.is_available() else "cpu"

    out = retrieve(
        args.query,
        k=args.k,
        mac_only=args.mac_only,
        db_dir=args.db,
        model_dir=args.model,
        device=device,
    )

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return

    for item in out:
        print(f"\n[{item['rank']}] score={item['score']:.3f}  mac={item['mac']}  {item['source']}")
        print(f"    {item['title']} ({item['year']}) {item['journal']} doi:{item['doi']}")
        if item["section"]:
            print(f"    section: {item['section']}")
        print(f"    ---\n    {item['snippet'].replace(chr(10), ' ')}")


if __name__ == "__main__":
    main()
