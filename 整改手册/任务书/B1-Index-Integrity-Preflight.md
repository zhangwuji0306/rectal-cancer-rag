# 任务书 B1：Index Integrity Preflight

## 目标

在检索或构建前发现 manifest、Chroma、模型和路径不一致，禁止静默生成空库。

## 执行内容

实现 index_preflight()，检查 DB directory、collection、collection count、manifest、manifest total_chunks、source count 和 model fingerprint。

任一关键项不一致都抛出 IndexIntegrityError。

将 rag_core cache 按 model_path、device、db_path、collection 分键，或改为独立 RAGRetriever 实例。

## 验收

manifest 存在但 chroma_db 删除时，build_index 必须失败；DB A 与 DB B 不得复用错误 collection；正常索引通过 preflight。

完成后提交独立 commit，并暂停等待审查。

