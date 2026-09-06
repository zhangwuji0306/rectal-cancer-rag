# 任务书 C4：Evidence-Aware Ranking

## 目标

在 relevance candidate pool 内透明地引入质量信息，避免质量指标变成第一阶段过滤器。

## 执行内容

比较 relevance only、加入 journal quality、external citations、corpus citations、publication type 及 full model 的 ablation。

可从 70% relevance、10% journal quality、10% external citations、10% corpus citations 的透明基线开始，但不预设为最终权重。

人工检查 30–50 个 query，关注高 IF 无关文章上浮、新文章被压制、相关低引用文章被误降。

## 验收

报告 Recall、nDCG、MRR、不同年份和 publication type 的分层变化；质量字段只能在相关候选内生效。

完成后提交独立 commit，并暂停等待审查。

