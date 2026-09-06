# 任务书 D1：Evidence Pack

## 目标

为生成模型提供结构化、可定位、已去重的证据包，而不是直接传递杂乱 top-k chunks。

## 执行内容

每条证据至少包含 paper_id、title、DOI、PMID、section、passage、semantic score 和 quality metadata。

同一 paper 的多个 passage 先整合，并保留每个 passage 的定位信息。

## 验收

证据包可从检索结果重建；每条 passage 都能回指原文和文献身份；重复论文不会造成不可解释的证据膨胀。

完成后提交独立 commit，并暂停等待审查。

