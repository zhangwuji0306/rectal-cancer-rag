# 任务书 B2：正式 Gold Retrieval Benchmark

## 目标

建立后续所有检索改进共用的人工标注评估集。

## 数据集

最低 100 queries，推荐 150–300。覆盖 MRI staging、EMVI/CRM、黏液表型、新辅助治疗、TNT/CRT、pCR/TRG、DFS/OS/复发、手术、病理、CEA/MSI、radiomics、deep learning、guidelines、trial names、精确术语和宽泛问题。

同时包含短 query、自然语言长 query、精确关键词、缩写和同义词。

相关性分级：3 直接回答，2 强相关，1 部分相关，0 不相关。按 70% development、30% locked test 划分。

## 指标与规则

记录 Recall@5/10/20/50、MRR@10、nDCG@10/20、UniqueRelevantPaperRecall；科研检索优先 Recall。

test 集冻结后不得用于反复调参。标签必须人工审核，禁止根据指标修改 relevant。

## 验收

每条 query 有来源、标注者、标注时间和 relevance 依据；dev/test 边界清楚；test 集具备冻结记录。

完成后提交独立 commit，并暂停等待审查。

