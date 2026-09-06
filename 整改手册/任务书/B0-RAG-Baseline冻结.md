# 任务书 B0：RAG Baseline 冻结

## 目标

在改变 retrieval 前建立可复现 baseline。

## 记录

记录 normalized document count、unique content count、indexed papers、chunks、duplicate groups、缺失 DOI/PMID/title/year、embedding model、model revision、chunk size、overlap 和 Chroma version。

运行现有测试、manifest reconcile 和当前检索评估。

若没有正式 qrels，明确记录 NO_VALID_GOLD_SET，不得用少量 query 宣称完成 benchmark。

## 验收

baseline 文件可由实际产物复核；配置和版本字段完整；评估缺失时明确标记而不是填零。

完成后提交独立 commit，并暂停等待审查。

