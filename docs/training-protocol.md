# 训练协议

每个阶段必须使用全新 Codex 会话，并且只显式调用指定 Skill。

1. 盲解 V1：准备 solve，调用 `$cumcm-a-solve`，禁止参考材料，完成后冻结 `FROZEN_BLIND_V1.json`。
2. 独立审计：关闭原会话，准备 audit，调用 `$cumcm-a-audit`，先验哈希再复现，只写审计建议。
3. 审计后盲修订：关闭审计会话，准备 blind-revision，再调用 `$cumcm-a-solve`；只能用题目、V1 和审计，冻结 `FROZEN_BLIND_FINAL.json`。若无需修订，可在 audited 状态直接冻结 final。
4. 参考复盘：关闭盲修订会话，把用户选定的 2–4 篇材料放入 reference-vault 并登记 `reference_ids`；准备 reflection，调用 `$cumcm-a-reflect`，不得改历史。
5. 知识提案：只生成 candidate；Dummy 只生成 demo。
6. 跨题验证：至少两个结构独立、不是同题参数变体的正面案例。
7. 回归：修改 Skills、verified 知识或评分规则后运行旧题回归，三大维度分别守门。
8. 开发集：只评估改进，不直接提炼知识。
9. 最终考试：新会话、独立工作区、提交冻结后才能导入答案到独立评分区。

所有自动检查使用 `pass`、`fail`、`needs_review`。文件隔离只能发现明显泄漏，不能证明模型内部从未见过相似内容。
