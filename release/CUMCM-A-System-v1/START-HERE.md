# 从这里开始

1. 解压 ZIP 后，双击包内的 `Launch-CUMCM-A-System.ps1`。安装目录外还附有同名启动器时，两者都可用。
2. 先选菜单 1。启动器会自动定位 Python 3.11+、Codex CLI、`CODEX_HOME`、`gpt-5.6-sol`、`reasoning=max`、XeLaTeX、Git、推荐模式和 `user-cases`；无需手工改 YAML。
3. 默认模式是 `workflow-only`。菜单 11 可手动选择 `workflow-only` 或 `full-trained`；切换只影响以后创建的案例。
4. 菜单 2 创建新题：输入案例 ID、题面文件和数据文件路径。
5. 菜单 3 开始或继续完整盲解。系统按 Solve → Blind V1 冻结 → Audit → Revision-Correctness → Revision-Paper-And-Verification → 确定性 Revision Verification → Blind Final 冻结执行，并在 Blind Final 后停止。
6. 菜单 5 请求在当前模型阶段结束后的安全边界暂停；菜单 6 根据 `case-state.json`、run metadata 和检查点恢复，不重跑已完成阶段。
7. 菜单 7 验证 Blind Final。只有逐文件哈希通过后，菜单 8 才允许导入 2–4 篇当前题参考论文，并使用全新 Reflection Thread 复盘；Blind Final 不会被修改。
8. 菜单 9 导出论文和支撑材料。最终论文通常位于导出目录的 `submission/paper/`，冻结清单和导出报告位于导出目录根部。

不要手工修改 `frozen/`、`runs/`、`case-state.json`、`run.lock`、`runtime-supervisor*.json` 或任何 SHA-256 清单。题面、数据和参考论文只放在 `user-cases/<案例ID>/`，不要提交到公开仓库。

真实赛题、数据和参考论文只保存在用户案例目录，不进入发布包。
