# solve 阶段锁

本会话只允许显式调用 `$cumcm-a-solve`，不得调用其他阶段 Skill。
只读取 `allowed-paths.json` 中列出的内容；禁止：reference-vault、exam-vault、参考论文、赛题讲评、标准答案、candidate knowledge。
所有自动判断使用 pass、fail 或 needs_review；不得伪造复现、哈希或数学正确性。
