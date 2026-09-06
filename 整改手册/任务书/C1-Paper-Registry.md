# 任务书 C1：Paper Registry

## 目标

从 filename-centric 迁移到 paper-centric 的稳定身份管理。

## 执行内容

按 DOI → PMID → PMCID → normalized title hash 生成永久 paper_id。

registry 至少保存 paper_id、DOI、PMID、PMCID、title、authors、year、journal、publication_type、license、source、version、is_supplement、duplicate_of、content_sha256、metadata_updated_at。

区分 exact duplicate、near duplicate、alternate version、supplement；不得只依赖 SHA identical。

## 验收

同一文献的不同文件可归并；补充材料和版本关系可追溯；无法确定的近重复保留 confidence 或待审状态，不得静默删除。

完成后提交独立 commit，并暂停等待审查。

