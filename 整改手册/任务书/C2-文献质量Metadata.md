# 任务书 C2：文献质量 Metadata

## 目标

补充期刊和外部引用 metadata，为后续实验提供可审计输入。

## 执行内容

保存近五年期刊指标、指标年份、field-normalized percentile、外部被引数、citation source、更新时间和 citation rate。

缺失指标标记为 missing，不得等同于 0。

本 Stage 只存 metadata，不参与排序、不改变召回候选。

## 验收

每个外部字段都有来源和更新时间；原始值与归一化值可区分；缺失、失败和未查询状态可区分。

完成后提交独立 commit，并暂停等待审查。

