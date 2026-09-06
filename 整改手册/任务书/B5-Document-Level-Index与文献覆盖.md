# 任务书 B5：Document-Level Index 与文献覆盖

## 目标

避免同一论文多个 chunk 占满 top-k，提高不同相关论文的覆盖。

## 执行内容

增加 max_chunks_per_paper、target_unique_papers；初始可采用 max_chunks_per_paper = 3。

候选不足时动态扩大 candidate pool，直到达到目标文献数或候选耗尽。

为每篇文献建立包含 title、abstract、keywords、publication type、year、journal 的 document card；合并 passage candidates 与 document candidates。

## 指标

报告 UniquePaperRecall@20、UniquePaperRecall@50、mean_chunks_per_paper，并与 B4/B0 baseline 比较。

## 验收

确认同一论文不会异常占满 top-k，动态扩大候选池在达到目标或候选耗尽时均能稳定结束。

完成后提交独立 commit，并暂停等待审查。
