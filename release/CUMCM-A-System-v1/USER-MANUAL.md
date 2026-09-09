# 用户手册

## 模式

- `workflow-only`：使用冻结的流程、Solve/Audit Skills、论文模板和复现工具，不加载训练记忆。
- `full-trained`：在同一流程上额外提供 22 张冻结 `provisional_training` 卡；模型最多检索 5 张并记录 adopt/adapt/reject。用户可手工选择，系统不会删除训练记忆。

模式在创建案例时固化到案例状态；切换模式只影响以后创建的案例。

## 生命周期

1. `New-Case` 把用户选择的题面/数据复制到新案例，使用通用文件名并记录 SHA-256。
   每个案例同时得到本地 `scripts/check_phase.py`、`phase-lock.json` 和 `allowed-paths.json`。模型每阶段先运行门禁；门禁缺失或失败时必须停止，不能去父目录寻找替代工具。
2. `Run-Case` 执行四个独立 ephemeral 模型阶段。Solve 后生成不可覆盖的 Blind V1 冻结副本；最终阶段后先做不增加模型调用的确定性 Revision Verification，再生成 Blind Final 冻结副本。
3. `Pause-Case` 只写入暂停请求，不粗暴终止正在写文件的模型；当前阶段结束后停止。
4. `Resume-Case` 从 `case-state.json` 的下一个阶段继续，不重跑已完成阶段。
   若完整的 `run-metadata.json` 已落盘但状态尚未来得及推进，系统接管原 Thread 证据并完成本地门禁，不会再调用模型；若仅有部分输出，则新 Thread 只补齐当前阶段缺口并保留现场。
5. `Case-Status` 同时显示当前状态、活动锁、监督子 PID、Thread 和最近 run metadata；不要仅凭 PID 文件判断进程。
6. `verify-final` 逐文件复核 Blind Final 哈希。
7. `Add-References-And-Reflect` 在冻结门通过后才复制 2–4 篇参考论文，在隔离工作区启动全新 Reflection Thread，并在前后复核 Blind Final 哈希。
8. `Export-Submission` 复制冻结论文和支撑材料，并再次复核冻结哈希。

## 文件位置

- 案例：`user-cases/<case-id>/`
- 阶段事件：`user-cases/<case-id>/runs/`
- Blind V1/Final：`user-cases/<case-id>/frozen/`
- 复盘报告：`user-cases/<case-id>/reports/`
- 导出：由用户在菜单 9 指定全新目录。

不要把 `user-cases/`、模型认证目录或私密日志提交到公开仓库。
