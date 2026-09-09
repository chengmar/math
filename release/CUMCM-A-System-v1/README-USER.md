# CUMCM-A-System v1

这是一个本地 A 题建模工作流产品：先盲解和冻结，再独立审计、分段修订、确定性验证，最后由用户手工开放参考资料复盘。系统固定请求 `gpt-5.6-sol`、`reasoning=max`、`fallback=false`，并为每个阶段使用新的 ephemeral 会话。

普通用户请先读 `START-HERE.md`，再双击包内 `Launch-CUMCM-A-System.ps1`。命令行用户可直接运行 `scripts/Verify-System.ps1`。

发布包不含任何真实赛题、原始数据、参考论文、历史答案、认证文件或私密日志。用户案例默认写入本包的 `user-cases/`；该目录不得提交到公开仓库。
