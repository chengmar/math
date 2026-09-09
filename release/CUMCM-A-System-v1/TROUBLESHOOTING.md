# 故障排查

- 找不到 Python：安装 Python 3.11+，或把 `CUMCM_PYTHON` 设为可信 Python 可执行文件。
- 找不到 Codex CLI：安装/更新 Codex 桌面或 CLI，确认 `codex --version` 可运行。
- 模型不匹配：停止案例；本产品禁止从 `gpt-5.6-sol/max` 回退到其他模型或推理档。
- XeLaTeX 不可用：安装 XeLaTeX/MiKTeX，并让 `xelatex` 可从 PATH 找到。已有 `.tex` 和构建日志会保留。
- “案例已有运行锁”：先用 `Case-Status` 检查是否仍有活动进程；不要在活动进程存在时删除锁。
- 管理界面断网：不要再次点“开始”。本地 `runtime-supervisor.json`、PID、nonce 和 events 会继续更新；恢复网络后先看菜单 4。
- 阶段门禁缺失/失败：立即停止当前阶段，不要到产品目录外寻找 `check_phase.py`。新建案例应自带 `scripts/check_phase.py`、`phase-lock.json` 与 `allowed-paths.json`；若缺少其中任一项，请保留案例现场并重新解压完整发布包。
- CLI 已写 final-message 但没有退出：系统等待终止事件并只回收该次精确子进程树，不会重新调用模型。
- 暂停没有立即退出：这是安全设计。暂停在当前阶段完成并落盘后生效。
- 参考导入被拒绝：先运行菜单 7，确认 Blind Final 的逐文件哈希通过，并提供 2–4 个普通文件。
- 冻结篡改失败：保留现场；不要覆盖冻结件。使用未篡改备份或重新创建新案例。
