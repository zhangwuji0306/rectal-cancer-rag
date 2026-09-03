# Retrieval evaluation

`queries.jsonl` is a manually labelled query set. Each line contains a query
and the DOI or source filename(s) that count as relevant:

```json
{"query":"...","relevant":["doi:10....","PMID_123456.md"]}
```

Start from `queries.example.jsonl`, review each label against the source paper,
then run:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
E:\writing-rag\.venv\Scripts\python.exe E:\writing-rag\index\evaluate_retrieval.py `
  --qrels E:\writing-rag\evaluation\queries.jsonl --k 5,10
```

The reported Recall@k, MRR@k, and nDCG@k are retrieval gates only. They are
not evidence that the corpus is complete or suitable for clinical decisions.
