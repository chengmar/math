# Lessons Proposed

本目录只包含 `candidate` 知识，不修改冻结盲解，也不自动进入任何已验证知识库。四篇批准材料均未检出 Dummy 标记，因此这里不是 demo；若后续发现来源或材料身份有误，应把相应候选降为 `needs_review`，而不是补写证据。

## 文件导航

- `method-candidates.md`：可迁移的方法候选及其验证条件。
- `failure-modes.md`：本案例实际暴露的失败模式。
- `validation-patterns.md`：可复用的验证设计。
- `expression-lessons.md`：建模论文的表达与证据分层。
- `non-generalizable.md`：不能从本案例外推的内容。
- `manifest.yaml`：候选条目、来源和状态索引。

## 来源标签

- `independent`：Blind V1 在参考材料开放前已存在。
- `audit_revision`：Audit 后修正，但仍早于参考材料。
- `reference_learned`：比较批准材料后才形成。
- `mixed`：独立框架与参考启发的组合。

自动判断只使用 `pass`、`fail`、`needs_review`；知识等级一律为 `candidate`。
