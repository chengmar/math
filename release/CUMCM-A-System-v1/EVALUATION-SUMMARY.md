# 效果与证据摘要

2023A 两次最终评测均因完成性失败而永久封存，不能用于估计 workflow、memory 或 full-system 效应，也不能证明系统有效或无效。

2022A 已通过项目级污染审计，资格为 `eligible_with_metadata_exposure`：仅年份、题号、文件角色、大小、哈希及排除状态等物流元数据暴露；项目训练、知识库、运行案例和 Git 历史中未发现正文、答案或参考论文材料。作为公开历史题，基础模型预训练是否见过无法保证。

2022A 分段替代评测从已冻结预注册和网络中断现场恢复。B-Solve 的有价值部分输出只按预注册允许的单次 continuation 完成，并通过独立复现、清单校验、论文逐页检查和表格检查，形成不可覆盖的 `FROZEN_BLIND_V1`。

随后，B-Audit 在查找缺失的阶段检查器时成功枚举了当前臂之外的运行时目录，并读取了一个披露 trainer 路径的安装元数据文件。虽然没有观察到 A/C、参考论文或训练内容被读取，且冻结 V1 哈希未变化，但这已经违反评测隔离合同。调用被精确终止，A/B/C 重新封闭；协议不允许以技术重试反复运行到成功。

因此 2022A 的最终状态是 `completed_without_valid_three_arm_effect_estimate`：没有 Blind Final、匿名评分或参考论文后置核验；`workflow_effect`、`memory_effect` 和 `full_system_effect` 均为 `null`，训练记忆效果为 `unverified`。这既不能证明系统有效，也不能证明系统无效。

生产默认模式最终确定为 `workflow-only`。用户仍可手动选择 `full-trained`，但 22 张训练记忆保持 `provisional_training`，不宣称已验证增益。
