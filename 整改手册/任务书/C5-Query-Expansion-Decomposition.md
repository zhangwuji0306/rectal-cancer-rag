# 任务书 C5：Query Expansion / Decomposition

## 目标

提高复杂科研问题的相关文献覆盖，同时保持原始 query 可追溯。

## 执行内容

对复杂 query 生成受 max_subqueries 限制的子查询；保留原 query，执行 union、dedupe、rerank。

避免扩展改变原问题意图；记录每个候选来自哪个子查询。

## 验收

在 benchmark 上报告 relevant-paper coverage 与 Recall@50，并抽样检查子查询是否引入明显无关主题。

完成后提交独立 commit，并暂停等待 Phase C 审查。

