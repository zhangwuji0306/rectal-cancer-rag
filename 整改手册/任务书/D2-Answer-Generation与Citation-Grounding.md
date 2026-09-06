# 任务书 D2：Answer Generation 与 Citation Grounding

## 目标

使每个重要医学结论都能回溯到检索到的证据，并在证据不足时明确不确定性。

## 执行内容

内部 answer schema 至少包含 claim、evidence_ids、confidence。

模型不得引用未检索到的文献，不得用自身参数知识补成文献结论。evidence insufficient 时 abstain 或明确说明不确定。

冲突证据分别展示 supporting A 与 supporting B，不强行合并。

## 验收

测试支持结论、证据不足、冲突证据、未检索 DOI 和多 passage 同文献场景；生成结果中的 DOI 必须存在于 Evidence Pack。

完成后提交独立 commit，并暂停等待审查。

