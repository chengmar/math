使用 $cumcm-a-solve 对当前案例进行完全盲解。

本会话只能执行 solve 阶段。不得调用 audit、reflect 或 evaluate；不得读取参考论文、讲评、标准答案或参考实现。只能读取 allowed-paths.json。先做简单基线，再比较候选；主要结果由代码生成并验证。完成论文、代码、图表和 solution-report，但不冻结、不复盘。

禁止联网搜索当前题目、题名、答案或现成解答。只能在当前最小复制工作区内工作。

如果 `reports/training-memory-retrieval.json` 存在，逐张检查已复制的 `provisional_training` 卡，并在 `reports/training-memory-usage.md` 中选择 adopt、adapt 或 reject。最多使用 5 张，允许全部拒绝；不得读取原始 Candidate，不得把训练记忆写成 verified 或 machine_verified，也不得为了证明跨题学习而强行增加不合适的复杂模型。
