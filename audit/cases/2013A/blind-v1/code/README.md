# 代码说明

本目录只实现当前盲解所声明的模型，不下载数据，也不访问题面中的网址。

- `solve.ps1`：读取 `model-parameters.json`，核验白名单输入身份，生成全部关键 JSON/CSV 与 PNG 图。
- `verify-paper.ps1`：把 `results/summary.json` 的关键数字与 `paper/paper.md`、`paper/main.tex` 中的结果标记逐项核对。
- `reproduce.ps1`：连续运行两次 `solve.ps1`，比较其声明的所有确定性输出 SHA-256。
- `run-all.ps1`：顺序运行求解、论文一致性检查和双次重跑检查。

在工作区根目录运行：

```powershell
pwsh -NoProfile -File .\code\solve.ps1
pwsh -NoProfile -File .\code\verify-paper.ps1
pwsh -NoProfile -File .\code\reproduce.ps1
```

脚本固定记录种子 `2013`，但当前模型本身不使用随机数。`pass` 只表示实现或不变量检查通过；视频缺失和外部有效性保持 `needs_review`。
