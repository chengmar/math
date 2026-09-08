# C：full-trained

与 B 使用完全相同的冻结工作流；唯一额外输入是当前工作区内只读的 22 张 `provisional_training` 卡。

Solve 开始时读取只读索引，按适配性检索最多 5 张。对每张检索卡记录 `adopt`、`adapt` 或 `reject`，并记录 `decision_influenced`、`validation_added`、`complexity_added`、`observed_risks`。允许采用 0 张，禁止为获得实验优势强行套用。不得修改卡片、状态、Skills 或训练记忆。

必须生成 `submission/blind-v1/training-memory-selection.json` 和 `submission/blind-v1/training-memory-usage.md`，修订阶段只可延续已冻结的选择记录。

