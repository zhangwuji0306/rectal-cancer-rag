# 任务书 D4：CI、Branch Protection 与发布治理

## 目标

建立可重复验证、受控发布和合规的公开仓库边界。

## CI

GitHub Actions 至少运行 unit tests、integration tests、schema validation、index integrity、retrieval regression 和 secret scan。

随后配置 main branch protection 与 required status checks。

## 仓库治理

README 至少说明 Purpose、Architecture、Quick Start、Corpus Policy、Retrieval、Evaluation、Compliance 和 Known limitations。

先形成清理清单并独立审查，再处理 debug scripts、runtime SQLite、临时报告、驱动二进制和 machine-specific artifacts。

公开仓库不得包含 PDF corpus、converted article full text、Chroma DB、private backup、API keys 或 personal credentials。legacy 非 OA provider 不得作为生产默认路径。

## 验收

CI 在干净环境可运行；敏感文件扫描通过；公开发布边界有清单和审查记录；main 分支保护规则生效。

完成后提交独立 commit，并暂停等待最终独立审查。

