# 任务书 B4：Hybrid Retrieval

## 目标

结合 dense 与 sparse 检索，提高精确术语和语义问题的召回。

## 架构

Query 分别进入 BGE-M3 dense 与 BM25/sparse，使用 RRF 融合为候选池。

## 执行内容

实现 retrieve_dense()、retrieve_sparse()、fuse_rrf()，并支持 dense-only、sparse-only、hybrid 三种模式。

统一候选、去重、排序和评估接口。

## 验收

在锁定 benchmark 上比较 Dense、BM25、Hybrid，至少报告 Recall@20、Recall@50、nDCG@10，并保留 query-level 差异。

完成后提交独立 commit，并暂停等待审查。

