# 任务书 C3：Corpus Citation Graph

## 目标

从参考文献构建可审计的 corpus citation graph，供后续分析而非直接替代相关性。

## 执行内容

按 DOI、PMID、title fuzzy match 匹配引用；模糊匹配保存 confidence，低 confidence 不建立 edge。

计算 in_degree、out_degree、PageRank，并保存 citation_graph_version。

明确区分 citation graph 与 relevance；不得让高引用旧论文自动压过新论文。

## 验收

抽样核验引用边、低 confidence 过滤、版本号和重复运行稳定性；错误匹配可定位到原始参考文献。

完成后提交独立 commit，并暂停等待审查。

