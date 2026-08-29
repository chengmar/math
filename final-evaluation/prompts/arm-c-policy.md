# C 组资源策略

使用与 B 组字节相同的共同冻结工作流。唯一实验差异是可以读取当前工作区 `knowledge/training-memory/` 中冻结的 22 张 `provisional_training` 卡。

Solve 开始时先读取只读索引，按题目适配性选择最多 5 张。对被检索卡逐张记录 `adopt`、`adapt` 或 `reject`、理由、影响的模型决策、增加的验证、增加的复杂度和潜在负迁移风险。不得为证明有效而强行使用；选择 0 张也必须如实记录。

必须生成 `training-memory-selection.json` 和 `training-memory-usage.md`。不得修改卡片或把状态升级为 `machine_verified`/`verified`。

