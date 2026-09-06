# 任务书 B6：Semantic Reranker

## 目标

在高召回候选池上进行语义重排，提高前排相关性而不牺牲召回。

## 架构

Hybrid retrieval → 100–200 candidates → semantic reranker → 20–50 papers。

## 执行内容

接入 cross-encoder 等 semantic reranker。reranker 只能重排第一阶段候选，不得替代 recall retrieval。

## 验收

在锁定 benchmark 上报告 nDCG@10、MRR@10、Recall@20。要求 nDCG/MRR 有提升且 Recall 无显著下降，并记录失败 query。

完成后提交独立 commit，并暂停等待 Phase B 审查。

