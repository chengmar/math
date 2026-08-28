# CUMCM-A-Lab 2016A—2021A 正式训练完成报告

生成时间：2026-08-28（Asia/Shanghai）

## 最终结论

本轮已从 2016A Reflection 启动前断点恢复，未重跑 2016A 的 Solve、Audit、Blind Revision 或既有两次昂贵独立复现；随后完成全新正式代次 2017A，并严格串行完成 2018A、2019A、2020A、2021A 的 Solve → Blind V1 → Audit → Blind Revision → Blind Final → Reflection 闭环。

训练队列现为停止状态：18 个案例 completed，0 个 completed_with_caveats，1 个 deferred_platform_safety，0 个 incomplete。系统状态为 `training_complete_ready_for_final_test`。最终测试保持 `test_sealed`、`consumed=false`，未启动、未读取正文、未导出内容。

## 案例状态

| 范围/案例 | 最终状态 | 说明 |
|---|---|---|
| 2003A | deferred_platform_safety | 保留平台安全延期；未打开参考材料；未生成训练记忆 |
| 2004A—2015A | completed | 复用并核验既有冻结与磁盘证据，未重做已完成阶段 |
| 2016A | completed | 仅执行新的 Reflection 和本地收尾 |
| 2017A | completed | 旧输出保留为失效审计记录；正式代次从新 Solve 会话开始 |
| 2018A | completed | 完整闭环 |
| 2019A | completed | 完整闭环 |
| 2020A | completed | 完整闭环 |
| 2021A | completed | 完整闭环；Blind Final、Reflection 与完成屏障均通过 |

## 训练记忆与 Candidate

- 最终 `provisional_training`：22 张，未超过活动上限 25。
- `provisional_at_risk`：0。
- 有效 Candidate 总数：323；其中本轮 2016A—2021A 产生 123。
- 历史 2014A 有一个旧候选包不满足当前 schema，按既有证据排除出有效 Candidate 统计；不改变 2014A 已完成状态。
- `machine_verified`：0；`verified`：0；未发生自动晋级。
- 正式 retrieval-log 中 2016A、2017A、2018A、2019A、2020A、2021A 的检索卡均为空，因此每年实际使用的训练记忆 ID 均为“无”。
- 22 张训练记忆 TM-001—TM-022 的实际使用年份均为空；adopt=0、adapt=0、reject=0、retrieved_only=0。
- 可核验的跨案例正面证据记录为 0，负面证据记录为 0；卡片内的 failure_modes/counterexamples 被保留，但不冒充实际跨案例使用证据。

## 最终质量门禁

- Trainer pytest：199 passed。
- 全量回归：pass。
- Git 语料泄漏守卫：pass，检查 509 个文件，0 findings。
- Trainer secret 扫描：pass；最终工作树无改动文件，0 findings。
- 脱敏导出扫描：pass，检查 1083 个文件，0 findings。
- 导出内容计数：真实题面 0、参考论文 0、原始数据 0、认证文件 0、最终测试内容 0。
- 脱敏导出自身 pytest：199 passed。
- 发布内容重克隆：1081 个内容哈希全部匹配，0 failures；重克隆 pytest 199 passed；重克隆脱敏扫描 pass。

## 逐项完成记录

1. **本轮起始断点**：2016A Reflection 启动前；Blind V1、Audit、Blind Final 和两次既有独立复现均已存在且通过核验。
2. **恢复时提交**：`b45346699882f9e97f602d3667276580d6107457`。
3. **测试基线**：184 passed；最终提升为 199 passed。
4. **2016A 是否重新调用 Solve**：否。
5. **2016A 是否重新调用 Audit**：否。
6. **2016A 是否重新调用 Blind Revision**：否；也未重复两次既有昂贵复现。
7. **2016A Reflection Thread ID**：`01a04396-ee4c-77b0-bb90-5ae0071edaa2`。
8. **2016A 最终状态**：completed；完成提交 `b926ce4`。
9. **旧 2017A 如何处置**：保留旧日志与失效证据；旧模型选择、代码、数值、论文和 Thread 上下文未进入新正式 Solve，正式计数从新代次开始。
10. **新 2017A Solve Thread ID**：`01a043ff-8466-7992-86f8-36e8f5e73625`。
11. **2017A 最终状态**：completed；完成提交 `df7bb32`。
12. **2018A 最终状态**：completed；完成提交 `fe29643`。
13. **2019A 最终状态**：completed；完成提交 `de1a9a8`。
14. **2020A 最终状态**：completed；完成提交 `61a2412`。
15. **2021A 最终状态**：completed；年度完成提交 `9cbb570`。
16. **completed 案例**：2004A—2021A，共 18 个。
17. **completed_with_caveats 案例**：无。
18. **deferred 案例**：2003A，原因为平台安全延期。
19. **每年使用的训练记忆 ID**：2016A—2021A 均为无；正式 retrieval-log 没有检索卡记录。
20. **provisional_training 最终数量**：22。
21. **provisional_at_risk 数量**：0。
22. **Candidate 总数**：323 个有效 Candidate；本轮 2016A—2021A 为 123 个。
23. **machine_verified 数量**：0。
24. **verified 数量**：0。
25. **全部 pytest 结果**：Trainer 199 passed；脱敏导出 199 passed；远端重克隆 199 passed。
26. **回归结果**：pass。
27. **Git 泄漏结果**：pass；最终 Trainer 检查 509 个文件，0 findings；导出扫描 1083 个文件，0 findings。
28. **实际模型与 reasoning**：2016A—2021A 共 24 个正式阶段会话，actual_model 全部为 `gpt-5.6-sol`，reasoning 全部为 `max`，全部为 ephemeral。
29. **是否发生 fallback**：否，fallback_detected=false。
30. **是否发生答案泄漏**：未检测到；阶段泄漏守卫、Git corpus leak 与导出扫描均 pass。
31. **活动进程数**：真实训练入口进程 0。
32. **PID、lock、nonce 状态**：PID 不存在；lock 不存在；nonce 为空。
33. **knowledge-snapshot-before-2023 路径**：`<LAB_ROOT>\reports\knowledge-snapshot-before-2023.json`。
34. **knowledge-snapshot 哈希**：SHA-256 `6c261d4a226863a2524017e9ee50c1be25985de1b74637be89581daffccd1561`。快照记录源提交 `df05f8c9865ba092d58c52425321addb6d455482`、Codex CLI `0.150.0-alpha.8`、AGENTS/四个 Skills/训练记忆/模型策略哈希。
35. **GitHub full-training-complete 提交**：脱敏内容根提交 `dadbb3299ea30ad4daf323de2704d148f12aa4fb`；本报告随后作为报告封装提交追加，不覆盖 main 历史且不 force push。
36. **远端重克隆结果**：从 `full-training-complete` 独立重克隆内容提交 `dadbb32` 到 `<LAB_ROOT>\review-export\reclone-full-training-complete-dadbb32`；HEAD 精确匹配；1081 个内容哈希全匹配；199 tests；脱敏扫描 pass；五类敏感内容导出数均为 0。
37. **最终测试状态**：test_sealed。
38. **consumed 状态**：false。
39. **是否进入 training_complete_ready_for_final_test**：是；队列 stop_requested=true，训练暂停=true，新模型会话和自动下一阶段/下一案例均已禁用。

## 版本与发布摘要

- 最终本地训练分支：`training/full-corpus-v1`。
- 最终本地状态提交：`24c8ef9bc6cf2fd447b40c649f860553f6ba4297`。
- 远端 `full-training-progress`：`24c8ef9bc6cf2fd447b40c649f860553f6ba4297`。
- 脱敏导出源提交：`0ededa4115aaa9152c8d2c50be2b28b6a51461be`，源工作树干净。
- 脱敏内容根提交：`dadbb3299ea30ad4daf323de2704d148f12aa4fb`。
- 脱敏导出目录：`<LAB_ROOT>\review-export\math-audit`。

训练已停止。未自动启动最终测试。
