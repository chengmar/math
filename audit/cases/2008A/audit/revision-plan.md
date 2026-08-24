# 证据化修订计划

- overall_status: `needs_review`
- constraint: 不修改当前 `frozen-solution`；不在本会话跨阶段 resume。

## P0：恢复可审计的冻结来源链

1. `needs_review`：由人工确定权威的 `code/solve.py`、唯一顶层复现命令及对应输出集合。
2. `needs_review`：处理代码哈希冲突。要么恢复 SHA-256 为 `a6f8bdf0...40c6` 的精确代码，要么用当前权威代码从原始输入重建所有产物并生成新的运行清单；不得只改清单数字。
3. `needs_review`：在受控来源工作区生成新的冻结载荷。冻结清单应使用规范化相对路径，列出每个载荷文件的字节数和 SHA-256，并明确清单自身是否排除及排除规则。
4. `needs_review`：将交付冻结状态设为 `pass`，记录冻结时间、清单版本和唯一冻结标识；不要在 audit 副本中事后改写 `delivery.frozen`。

## P1：消除复现协议歧义

1. `needs_review`：说明 `--monte-carlo 100` 与 `--bootstrap 120` 是同一流水线的两个步骤还是两条独立流水线。
2. `needs_review`：若保留两条流水线，明确执行顺序、共享输入、输出目录、随机种子、重复次数以及论文中的每个关键数字来自哪条流水线。
3. `needs_review`：用一个顶层脚本执行全部必要步骤，并让失败退出码向上传播；清单记录准确命令、依赖版本、stdout/stderr 保存位置和输出哈希。

## P2：重新进入独立 audit

1. `needs_review`：把新的冻结载荷复制到一个全新的最小 audit 工作区，不要复用本会话状态。
2. `needs_review`：先验证 audit phase lock、完整冻结清单和全部逐文件 SHA-256。
3. `needs_review`：只有前置门全部 `pass` 后，才在干净环境安装锁定依赖并复跑，记录命令、stdout、stderr、退出码和产物差异。
4. `needs_review`：随后完成数学/量纲/约束、数值稳定性、泄漏与过拟合、反例、以及论文—代码—图表一致性审查。

完成判据：新的 audit 报告必须能把“冻结载荷完整性”“代码—产物来源链”和“干净复现”分别判为 `pass`；任一项为 `fail` 时不得声称解答已通过审计。
