# Proposed lessons

本目录只收录由单一案例和三篇批准材料提炼的候选经验。全部条目的 `knowledge_state` 均为 `candidate`，不得据此直接升级为更高知识状态；本次没有 Dummy 条目，也没有生成 demo。

自动判定只使用 `pass`、`fail`、`needs_review`：

- `pass`：仅表示本案例中相应证据检查通过；
- `fail`：表示本案例中发现了可复算错误、内部矛盾或明确不可接受的推断；
- `needs_review`：表示迁移性、外部有效性或工程可行性仍需新案例/实验审查。

目录内容：

- `method-cards.yaml`：候选方法卡；
- `failure-modes.yaml`：候选失败模式；
- `validation-patterns.yaml`：候选验证模式；
- `expression-lessons.yaml`：候选表达经验；
- `non-generalizable-lessons.yaml`：不得直接泛化的案例常数和结论；
- `problem-specific-facts.yaml`：本题特异性事实与证据状态。

任何后续采纳至少需要独立案例复现；涉及硬件可行性、空间传播或外部正确性的条目还需要实验或更高保真模型证据。
