# AGENT.md — 直肠癌文献爬取项目

> 更新日期：2026-08-28（已纳入 E:\writing-rag 主项目的一体化维护）。本文档只保留最新爬取方案与要点；历史明细见 reports 各 CSV。

## 项目目标

从 `pubmed-RectalNeop-set.nbib`（9171 条直肠癌文献）构建 RAG 语料：**索引信息.csv**（全量元数据 + 状态）+ **PDF 语料库**（命名 `PMID_<pmid>.pdf`）。

**范围策略（暂时）**：爬取 **2000 年及以后**全部文献（含 2022+，共 7589 条）。2000 年前 1582 条已**封存**（Status=archived，不爬取）。

## 与 RAG 主项目的一体化维护

本目录位于 E:\writing-rag\直肠癌文献爬取，是当前项目的一部分。智能体可在用户授权范围内维护文献检索、下载脚本、配置、SQLite 队列、报告和 PDF 语料。

一体化顺序：下载并更新本目录内的任务状态 → 运行 05_report.py 和 14_refresh_corpus_meta.py → 将新增且未处理的 PDF 增量放入上级 papers\ → 在上级项目运行 gen_pmid_mapping.py → 运行 batch_convert.py 的转换/入库流程 → 用 reconcile_manifest.py 校准向量清单。

pdfs_merged\ 是下载主副本，不因下游转换而删除；原始 NBIB 默认不改写；令牌和 API 凭据只放进程环境，不写入文件。

## 数据画像
| 项目 | 数值 |
|---|---|
| nbib 总记录 | 9171（全部含年份） |
| 2000 年前（已封存） | 1582 |
| 爬取池（2000 年及以后） | 7589（= 4707 + 2022+ 2882；2022+ 有 DOI 99.5%、有 PMC 55%） |

## 目录结构

```
E:\writing-rag\直肠癌文献爬取\
├── pubmed-RectalNeop-set.nbib   # 输入（勿改）
├── config.json                  # 镜像/限速/暂停/阈值配置
├── 索引信息.csv                 # 19 列元数据 + Status/PDFPath/Note（UTF-8 with BOM）
├── pdfs_merged\                 # ★唯一 PDF 语料库：2422 篇 + 语料元数据.csv + summary.txt
├── tasks.sqlite                 # ★唯一任务库：tasks 全量当前状态(9171) + run_history 批次明细(5336)
├── reports\                     # archive_pre2000.csv / run_history.csv / download_report.csv / summary.txt / 抽样与分批清单
├── logs\downloader.log          # 下载引擎日志
└── scripts\
    ├── common.py                # 公共：配置/日志/SQLite/PDF 校验/挑战识别/标题相似度
    ├── 01_parse_nbib.py         # nbib → 索引信息.csv
    ├── 02_build_queue.py        # CSV → 任务队列（默认 2000 年及以后，跳过 archived；支持随机抽样）
    ├── 03_downloader.py         # 核心下载引擎（断点续跑；只处理 pending）
    ├── selenium_fallback.py     # 挑战 Selenium 兜底（独立测试：--mirror ... --headed）
    ├── 05_report.py             # 报告 download_report.csv + 回写 索引信息.csv
    ├── 06_doi_lookup.py         # DOI 多源反查（已停用，见已知限制）
    ├── 09_merge_pdfs.py         # 历史合并脚本（来源目录已删，勿再运行；增量刷新用 14）
    ├── 10_archive_pre2000.py    # 封存 2000 年前（幂等）
    ├── 11_merge_sqlite.py       # 重建单一 tasks.sqlite（tasks+run_history，原子替换；批次库随跑随删）
    ├── 12_build_pmc_batches.py  # ★PMC 优先分批（从新到旧，每批 N 篇；批次库内一律置 pending）
    ├── 13_sync_batch.py         # ★批次库 → reports\run_history.csv（同 run 可替换重跑）
    └── 14_refresh_corpus_meta.py# ★刷新 pdfs_merged\语料元数据.csv（增量，--batch-label 标记新篇）
```

## 当前爬取方案

- **范围**：`03_downloader.py --min-year 2000`（不设上限）；`02_build_queue.py` 默认只入队 2000 年及以后且跳过 Status=archived（封存不会复活）。
- **默认通道链（合规 OA 优先）**：**Europe PMC OA 直连**（有 PMC 字段一律最优先；无 PMC 且无 DOI 时按 PMID 搜索；官方通道无反爬）。Sci-Hub/非 OA 通道默认关闭，只有机构合规与法务批准后才可显式开启。NCBI PMC 直连已弃用（2026-08 起对全新会话返回 JS "Preparing to download" 中转页）。
- **PMC 优先分批（当前主策略）**：有 PMC 且未完成（pending+not_found+failed）按 **year DESC 每 400 篇一批**（12）→ 03 下载 → 13→11→05→14 同步后跑下一批。批次库内 status 一律重置 pending（引擎只处理 pending；not_found/failed 即重试对象）。
- **镜像**：仅在合规批准并显式开启非 OA 通道时使用 sci-hub.st / sci-hub.ee；默认不访问镜像。
- **引擎要点**：
  - 限速：每镜像请求间隔 3.0–5.0s 均匀随机，workers=3；
  - **周期防检测暂停**：连续镜像访问累计 200–300 次（每轮随机）→ 全池暂停 120–180s（每轮随机）；
  - **Altcha PoW 解算**：challenge=sha256(salt+str(n)) 暴力遍历 + `base64(JSON{...,took})` payload POST `/captcha/solution/<id>`，串行化；失败才走 Selenium 兜底；
  - `verify_ssl=true`（TLS 校验不可关闭）；Sci-Hub 默认不创建镜像会话；显式开启时 cookie 按镜像隔离并每 30min 刷新；
  - 挑战识别含 PDF 链接守卫（正常文章页模板常驻 altcha-widget，勿误判）；
  - PDF 校验：`%PDF` 头 + ≥30KB；状态机 `pending→downloading→done/failed/not_found`；`downloading` 超 15min 自动回收；
- **监测约定**：预估用时内不监测；超时后每 t=预估/10 检查一次。

## 命令速查

```powershell
cd E:\writing-rag\直肠癌文献爬取
# PMC 优先分批（从新到旧每批 400；0=全部批次；续跑用 --first-batch N）
python scripts\12_build_pmc_batches.py --batch-size 400 --max-batches 0 --first-batch 6
# 跑一批（--db 指向该批库，PDF 直达 pdfs_merged）
python scripts\03_downloader.py --db tasks_pmc_bN.sqlite --pdf-dir pdfs_merged
# 批次同步链（每批跑完后依次执行）
python scripts\13_sync_batch.py --run pmc_bN --db tasks_pmc_bN.sqlite
python scripts\11_merge_sqlite.py            # 重建 tasks.sqlite + run_history（批次库随之删除）
python scripts\05_report.py --pdf-dir pdfs_merged
python scripts\14_refresh_corpus_meta.py --batch-label pmc_bN-YYYYMMDD
# 重跑未完成条目（在 tasks.sqlite 上执行）：
UPDATE tasks SET status='pending' WHERE status IN ('not_found','failed');
# 解除封存（反操作）：
UPDATE tasks SET status='pending' WHERE year < 2000;
```

## 当前语料状态（2026-08-24）

- **pdfs_merged 共 2422 篇**（3867.8 MB，全部有效）；覆盖率 2422/7589 ≈ 31.9%；
- 待办（tasks.sqlite）：done 2419 / not_found 1075 / failed 21 / pending 4074；
- **PMC 优先批次 b1–b5 已完成**（共 2000 篇，从新到旧覆盖 2026~2020）：done 228+247+198+249+248 = **1170（58.5%）**，主要通道 europepmc:pdf；重试策略有效（b1/b2 未命中的新文献数日后由 Europe PMC 补回索引）；
- **剩余 PMC 队列：1313 篇**（pending 724 + not_found 578 + failed 11；2026 77 / 2025 67 / 2024 82 / 2023 96 / 2022 88 / 2021 71 / 2020 140 / 2019 132 / 2018 88 / 2017 83 / 更早 389），从 `--first-batch 6` 继续；
- 无 PMC 文献（约 4600 篇未处理）等待已批准的官方 OA 来源或人工授权来源。

## 已知限制

- 无 DOI 文献成功率极低；非 OA 下载通道默认关闭。DOI 多源反查已停用（实测命中率约 2% 且命中文献多未收录）——备选：人工核对/馆藏、Unpaywall；
- 镜像通道默认关闭；显式启用前必须完成机构合规/法务审批，并保持 TLS 校验。
- Europe PMC 对最新文献（如 2026）索引可能滞后（PMC 字段在但搜索无记录），数日后重试可补回；Unpaywall 通道未启用（API 强制要求真实邮箱，如日后启用需在 config 加 unpaywall_email）；
- `config.json` 的 `crossref_mailto` 建议换真实邮箱（进入 polite pool 提高限流额度）；
- 下载行为涉及版权；许可证必须逐条记录，发布或扩大使用范围前由机构合规/法务确认。
