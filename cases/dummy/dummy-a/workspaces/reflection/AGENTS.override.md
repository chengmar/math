# reflection 阶段锁

本会话只允许显式调用 `$cumcm-a-reflect`，不得调用其他阶段 Skill。
只读取 `allowed-paths.json` 中列出的内容；禁止：修改冻结盲解、直接升级 verified。
所有自动判断使用 pass、fail 或 needs_review；不得伪造复现、哈希或数学正确性。
