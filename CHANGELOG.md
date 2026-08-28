# 变更日志

## 未发布 - 2026-08-28

- 从 2016A Blind Final 后断点完成独立 Reflection、本地门禁与 completion barrier 恢复；保留首次完整会话证据，记录并中断一次仅由 Candidate 容器别名触发的冗余重试。
- Candidate 校验器兼容通用 `items` 容器，并在 Reflection 收尾中清理 OCR/文本派生目录；全量测试增至 185 项。
- 从 2016A 的 22 个 Candidate 中脱敏、去重并加入 3 张带完整适用边界的 `provisional_training` 卡；活动卡片增至 18，未提升 `machine_verified` 或 `verified`。

## 0.2.0 - 2026-08-17

- 增加 2003A—2021A 训练、2022 排除和 2023A 最终测试封存的固定语料划分。
- 增加只读 inventory、事务式 dry-run/apply/resume 导入、哈希复核、安全解压与 Git 真实语料守卫。
- 增加外置 runtime case、19 题升序训练队列、独立阶段会话、锁/PID/停止/恢复 Autopilot。
- 增加独立 Curator、Shadow Evaluation 与 `machine_verified` 跨年机器验证门。
- 加固 phase-lock、同案例参考材料边界、真实 Vault 哈希索引和严格 fail-closed 泄漏检测。
- 保留并通过原有 29 项测试；批量与安全测试扩展到 88 项。

## 0.1.0 - 2026-08-16

- 初始化训练、审计、复盘、盲测四阶段隔离框架。
- 加入状态机、冻结哈希、泄漏检查、知识升级门槛、论文检查与评分工具。
- 加入 Dummy 生命周期与自动测试。
