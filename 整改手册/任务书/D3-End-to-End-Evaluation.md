# 任务书 D3：End-to-End Evaluation

## 目标

评估完整系统的证据质量，而不是只评价语言流畅度。

## 执行内容

准备至少 50–100 个问题并人工核验 retrieval recall、citation correctness、citation completeness、claim support、unsupported claim rate、hallucination、answer completeness 和 conflict handling。

重点记录 invalid citation rate、unsupported claim rate、missing citation rate。

评估集、标注、答案和证据包必须可复核；不得用生成模型自动标注后直接作为真值。

## 验收

报告按问题和总体汇总指标，明确失败案例、证据不足案例和冲突处理案例。

完成后提交独立 commit，并暂停等待审查。

