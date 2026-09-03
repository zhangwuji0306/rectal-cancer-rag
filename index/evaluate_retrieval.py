"""Evaluate literature-level retrieval against a manually labelled JSONL set.

Each line in the qrels file must contain:
    {"query": "...", "relevant": ["doi:10...", "PMID_123.md"]}

The evaluator reports Recall@k, MRR@k, and nDCG@k after DOI/source
deduplication. It never generates an answer.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "index"))
from rag_core import retrieve_literature  # noqa: E402


def normalise_key(value: str) -> str:
    value = (value or "").strip()
    if value.lower().startswith("doi:"):
        return value[4:].strip().lower()
    return value.lower()


def result_key(item: dict) -> str:
    doi = normalise_key(item.get("doi", ""))
    return doi or normalise_key(item.get("source", ""))


def ndcg(binary_relevance: list[int], relevant_count: int, k: int) -> float:
    actual = sum(value / math.log2(index + 2) for index, value in
                 enumerate(binary_relevance[:k]))
    ideal = sum(1 / math.log2(index + 2)
                for index in range(min(relevant_count, k)))
    return actual / ideal if ideal else 0.0


def evaluate(qrels: list[dict], ks: list[int], **retrieve_kwargs) -> dict:
    totals = {metric: {str(k): 0.0 for k in ks}
              for metric in ("recall", "mrr", "ndcg")}
    per_query = []
    for row in qrels:
        relevant = {normalise_key(item) for item in row["relevant"]}
        hits = retrieve_literature(row["query"], k=max(ks), **retrieve_kwargs)
        keys = [result_key(item) for item in hits]
        binary = [int(key in relevant) for key in keys]
        query_metrics = {"query": row["query"]}
        for k in ks:
            found = sum(binary[:k])
            first = next((index + 1 for index, value in enumerate(binary[:k])
                          if value), None)
            values = {
                "recall": found / len(relevant) if relevant else 0.0,
                "mrr": 1 / first if first else 0.0,
                "ndcg": ndcg(binary, len(relevant), k),
            }
            for metric, value in values.items():
                totals[metric][str(k)] += value
            query_metrics[str(k)] = values
        per_query.append(query_metrics)
    n = len(qrels) or 1
    averages = {
        metric: {k: round(value / n, 4) for k, value in values.items()}
        for metric, values in totals.items()
    }
    return {"queries": len(qrels), "averages": averages, "per_query": per_query}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate literature retrieval")
    parser.add_argument("--qrels", type=Path, required=True)
    parser.add_argument("--k", default="5,10", help="comma-separated cutoffs")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--mac-only", action="store_true")
    args = parser.parse_args()
    ks = sorted({int(value) for value in args.k.split(",") if int(value) > 0})
    qrels = [json.loads(line) for line in args.qrels.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    result = evaluate(qrels, ks, db_dir=args.db, model_dir=args.model,
                      device=args.device, mac_only=args.mac_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
