# 独立审计响应矩阵

本表只判断“建议修订是否有对应实现与证据”，不把修订完成等同于外部地点真实。所有状态仅使用 `pass`、`fail`、`needs_review`。

| 审计 ID | 修订状态 | 主要改动 | 验收证据 | 仍保留状态 |
|---|---|---|---|---|
| AUD-MATH-001 | `pass` | 问 4 固定 `G_pg`，使用 `pi(G_pg p_i)-Hq_i` 米制残差；另给 `G_gp` 像素重投影等价式 | `<SOURCE_FILE_REDACTED>` 3/3 `pass` | 无视频/控制点，完整方法与数值为 `needs_review` |
| AUD-STAT-002 | `pass` | 舍入文件重命名为 `rounding_only_conditional`；新增残差相关、三点时间块自助、污染、结构、时钟、公式截断压力测试 | `<SOURCE_FILE_REDACTED>`、`<SOURCE_FILE_REDACTED>` 等 | 总体统计区间 `needs_review` |
| AUD-ID-003 | `pass` | 输出日期—地点联合簇、条件分位数、簇间公里距离、前缀漂移和污染曲线；正文降至 0.1° | `<SOURCE_FILE_REDACTED>`、`<SOURCE_FILE_REDACTED>`、`<SOURCE_FILE_REDACTED>` | 外部真值 `needs_review` |
| AUD-VAL-004 | `pass` | 多项式次数只在前 14 点内滚动选择；主模型由训练段任务匹配决定；15–21 点只作最终测试 | `<SOURCE_FILE_REDACTED>` 无最终测试指标；所有 holdout 行 `final_test_used_for_selection=fail` | 最终测试不验证地点，参数有效性 `needs_review` |
| AUD-REP-005 | `pass` | 完整命令补齐 `-Rerun`；检查表写入源码 SHA-256；30 项确定性产物复跑并做空目录隔离重建 | `<SOURCE_FILE_REDACTED>` 30/30；`<SOURCE_FILE_REDACTED>` 34/34 行 `pass` | 动态时间元数据按声明排除 |
| AUD-PAPER-006 | `pass` | 排名拆为 `branch_rank/global_rmse_rank`；检查 TAB/NUL/裸 CR；状态计数由 CSV 核对 | 24/24 候选交叉检查；论文检查 99 `pass`、1 `needs_review`、0 `fail` | 唯一 `needs_review` 是无 LaTeX 引擎 |
| AUD-DATA-007 | `pass` | 数值强制转换前检查 null/空串/类型/有限性；工作表集合精确匹配 | `validate_outputs.ps1` 与当前数据审计 | 当前数据本身无空值 |
| AUD-CONSTRAINT-008 | `pass` | `s∈{-1,+1}`、`H>10^-6 m`、近零影端拒绝、重复时间拒绝、夜间真实高度+独立标志 | 四个边界反例均在独立验证中 `pass` | 真实地形/倾斜量级 `needs_review` |
| AUD-PROV-009 | `needs_review` | 依赖、命令、种子和允许来源统一写入 `reproducibility.yaml`；论文不使用未追溯背景引用作证据 | 本报告与复现清单 | 外部冻结 manifest 由外部脚本生成，本会话不修改 |
| AUD-VERIFY-010 | `pass` | 输入哈希改为代码内不可变清单并在 Office 打开前核对；交叉搜索动态覆盖全部公开候选 | 输入清单 `pass`；24/24 旋转/镜像候选 `pass` | 更早输入历史字节身份 `needs_review` |

## Major 项结果尺度

- 附件 1、3 的 RMSE/舍入二维上界分别为 4.90、3.98，纯舍入总体误差假设为 `fail`。
- 舍入条件扰动的相邻日地点簇跳变约 15.9–39.3 km。
- 前 8 点到全 21 点的地点漂移：附件 1 约 20.0 km、附件 2 约 31.1 km、附件 3 约 49.5 km。
- 附件 2 的 1 mm 单点/三点污染可改变约 210.5–229.7 km；普通最小二乘稳健性为 `fail`。
- 最终测试中，三次多项式在附件 1、3 的轨迹 RMSE 为 0.683、0.802 mm，小于物理模型的 1.040、0.911 mm；因此短弧预测不能证明地点。

## 当前交付门槛

- P0 数学方程、误差证据和联合不确定性报告：修订实现 `pass`，外部/总体结论继续 `needs_review`。
- P1 选型用途分离、30/30 确定性复跑和空目录隔离复现：`pass`。
- P2 空值、约束、排名语义、论文状态计数与源码哈希绑定：`pass`；LaTeX 编译因无引擎为 `needs_review`。
- 本会话不会冻结；由外部冻结脚本生成 `blind-final`。
