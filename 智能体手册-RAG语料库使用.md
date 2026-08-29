# 智能体速查：RAG 语料库

## 语料概况

截至 2026-08-28，Chroma collection `papers` 含：

- 2042 个唯一来源、56156 个文本块；
- 年份主要为 1999–2026，2035 个来源有年份元数据；
- 1952 个普通直肠癌来源、83 个 `mac=yes` 黏液腺癌来源、7 个元数据未知来源；
- 主题以直肠癌 MRI/影像诊断与分期、淋巴结、放化疗与新辅助治疗反应、手术及预后为主，并含黏液性结直肠癌的影像、病理、分子机制和治疗研究。

`mac` 是信息性标签，不是完整或权威的病理分类。库中有少量重复、元数据缺失和错误转换；关键结论必须回看原文。

## 命令行检索

在 `E:\writing-rag` 中：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe index\retrieve.py `
  'rectal cancer MRI lymph node metastasis' -k 8 --json --device cpu
```

只检索 `mac=yes`：

```powershell
.\.venv\Scripts\python.exe index\retrieve.py `
  'mucin pool response prognosis' -k 8 --mac-only --json
```

查询可用中文或英文；医学术语、缩写和英文同义词分别检索后合并，通常比单次长问题更稳。

## Python 调用

必须使用 `E:\writing-rag\.venv\Scripts\python.exe`：

```python
import sys

sys.path.insert(0, r"E:\writing-rag\index")
from rag_core import dedupe_by_doi, retrieve, summary_list

hits = retrieve(
    "MRI prediction of rectal cancer lymph node metastasis",
    k=12,
    mac_only=False,
    device="cpu",
)
papers = dedupe_by_doi(hits)
references = summary_list(papers)
```

- `retrieve()` 返回 chunk 级结果：`rank/score/source/mac/title/year/journal/doi/authors/section/snippet`；
- `dedupe_by_doi()` 按 DOI 合并同一文献的多个命中；无 DOI 时按 source 合并；
- `summary_list()` 生成不含 snippet 的文献级结构。

同一 Python 进程会缓存首次加载的模型和 collection；不要在同一进程中切换不同的 `db_dir`、`model_dir` 或 device。

## 使用原则

1. 用 2–4 个短查询覆盖主题、同义词、影像征象或结局；
2. 每次取较宽的 `k`，再按 DOI 去重；
3. 用 `source` 定位 `converted\已入库\<source>`，核对完整上下文；
4. 以 DOI、题名、年份和期刊生成引用候选；
5. RAG 只负责召回，不负责生成答案，也不保证引文正确性。

当前 `index\manifest.json` 已按 Chroma 实库校准；若后续并发写入造成漂移，先停止写入进程，再运行 `index\reconcile_manifest.py --dry-run`，确认后去掉 `--dry-run`。
