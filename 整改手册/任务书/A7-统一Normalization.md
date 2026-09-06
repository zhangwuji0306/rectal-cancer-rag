# 任务书 A7：JATS / TXT / PDF 统一 Normalization

## 目标

建立统一 normalized corpus，使不同正文格式进入同一后处理和 RAG 流程。

## 执行内容

JATS parser 至少识别标题、摘要、Introduction、Methods、Results、Discussion、Conclusion、表格及表题、图及图题、补充材料和参考文献。

输出 normalized/PMID_<pmid>.md，并保留 front matter：PMID、DOI、PMCID、title、year、journal、source、license、content_sha256。

正文采用稳定的标题层级。PDF 继续通过 MinerU 转换，但与 XML、TXT 共享 normalized Markdown 输出约定。

## 验收

用小型 XML、TXT、PDF fixture 检查章节、表格、图题、参考文献和 front matter 均可保留；同一文献不同来源的身份字段一致。

完成后提交独立 commit，并暂停等待审查。

