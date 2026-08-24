# 2009A 冻结解答独立审计报告

## 结论

总体状态：`fail`。

Q1--Q4 的物理公式、量纲和主要数值独立复算均为 `pass`；Windows PowerShell 5.1 下，正式入口也以退出码 0 重现了全部核心数值产物。失败来自交付与控制完整性，而不是前三问或 Q4 的主数值：原始 `.xls` 哈希在 Excel 解析前没有被固定，冻结包混有两条相互冲突且不能由唯一命令全量再生的管线，冻结 PDF 早于冻结 TeX 源；Q6 还存在低扭矩限幅失效、高增益速度反馈、未声明稳定域和缺少停止约束。硬件可行性为 `needs_review`。

未发现使用参考论文、题解或联网材料的静态证据；本次 audit 也未联网搜索题目。静态泄漏检查为 `pass`，历史行为的完全否定证明仍为 `needs_review`。

## 冻结门槛

| 检查 | 状态 | 证据 |
|---|---|---|
| audit phase lock | `pass` | `check_phase.py` 输出 `[PASS] audit 阶段锁有效` |
| allowed/forbidden 清单哈希 | `pass` | 两个实际 SHA-256 与外层 `phase-lock.json` 完全一致 |
| FROZEN 逐文件完整性 | `pass` | 111/111 文件无缺失、无大小差异、无 SHA-256 差异、无重复或越界路径 |
| FROZEN tree SHA-256 | `pass` | `path + NUL + sha256 + LF` 拼接重算为 `f774f688...`，与记录一致 |
| 冻结目录洁净性 | `fail` | 另有两个未列入清单的 `.pyc` 缓存 |
| 当前外层输入与冻结输入字节一致 | `fail` | `.doc` 一致；`.xls` 为 `d012...` 对 `2b217...` |
| 当前外层/冻结 `.xls` 数值语义 | `pass` | 468 行逐值相等，四个工况值相等 |

逐文件与树哈希通过后才读取解答正文和运行代码。两个未清单缓存不改变任何受保护文件，因此没有把它们误作哈希失败，但在 findings 中记为冻结洁净性问题。

## 干净复现

在 audit 根目录创建短期 clean workspace，只复制 50 个源文件；排除 61 个既有结果、图、PDF、日志、生成宏和缓存。正式命令为：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File code/run_all.ps1 `
  -Workspace <RUNTIME_ROOT>\2009A\workspaces\audit\.audit-repro-clean-593096e1 `
  -VerifyPaper
```

- 运行环境：Windows PowerShell 5.1、Python 3.12.13、NumPy 2.5.2、Matplotlib 3.11.1、PyYAML 6.0.3、Excel COM、MiKTeX XeLaTeX。
- 退出码：0；耗时：6.53 s。
- stdout：数据提取、数值生成、独立验证和总入口均报告 `[pass]`。
- stderr：只有四条 MiKTeX 未检查更新告警。
- 20 个生成物中 15 个与冻结版逐字节一致，包括 `<SOURCE_FILE_REDACTED>`、`input-metadata.json`、`metrics.json`、`run-manifest.json`、全部正式 CSV/图和 `generated-results.tex`。
- 差异 5 个：`main.aux`、`main.log`、`main.out`、`<SOURCE_FILE_REDACTED>`、`verification.json`。

数值复现状态为 `pass`，完整冻结产物复现为 `fail`。冻结目录共有 61 个结果类产物，但正式入口只生成 20 个；其余 41 个来自旧分析/预览管线。详细命令、环境、stdout、stderr、哈希和未再生清单见 `reproduction-report.json`。

PowerShell 7.6.4 兼容运行也退出 0，数值不变，但 CSV/JSON 编码使 `<SOURCE_FILE_REDACTED>`、元数据和 run manifest 的字节哈希改变。因此“Windows PowerShell 5.1 or compatible”只达到数值可复现，未达到字节可复现。

## 逐问数学与量纲审查

| 小问 | 状态 | 审计结论 |
|---|---|---|
| Q1 | `pass` | \(J_e=(F/g)r^2=51.9988857143\ \mathrm{kg\,m^2}\)，量纲正确 |
| Q2 | `pass` | 环形飞轮公式、三件惯量和 \(2^3\) 个组合完整；选 40.008312、补偿 11.990574 合理 |
| Q3 | `pass` | \(u=-(J_e-J_m)\dot\omega=(\Delta J/J_e)M_b\)，电流 174.687844 A，符号和量纲正确 |
| Q4 数值 | `pass` | 名义道路能量 52,150.200087 J；台架梯形耗能 49,242.288736 J；欠耗能 2,907.911351 J，5.576031% |
| Q4 验收 | `needs_review` | 题面没有合格阈值，不能自动判“合格/不合格” |
| Q5 理想模型 | `pass` | 边界时刻扭矩零阶保持在精确观测、外生扭矩模型下因果且公式正确 |
| Q5 物理验证 | `needs_review` | 只有同一附表扭矩的 model-in-the-loop 回放，无新台架试验 |
| Q6 实现 | `fail` | 限幅、稳定域、速度噪声、快速扭矩和停止状态约束不完整 |
| Q6 硬件验收 | `needs_review` | 电流、电压、热、扭矩变化率和传感器规格缺失 |

没有发现主公式的量纲错误。Q4 的一个小数值细节是：论文同时假设扭矩和转速线性时，精确乘积积分为 49,242.307590 J，不是 49,242.288736 J；差 0.018854 J，不改变欠耗能结论。

## 稳定性、约束与反例

1. 低扭矩限幅 `fail`：\(\widehat M=0.1\) N·m 的代数反例给出 300 kg·m² 等效指令，超过 ±30。
2. 速度噪声鲁棒性 `fail`：100 N·m、10 ms 时约 −0.097 rpm 的测量误差即可触发一侧饱和；0.1 rpm 噪声压力下峰值电流中位数为 270.12 A。
3. 惯量失配稳定域 `fail`：线性极点为 \(1-\gamma J_d/J_a\)；提交参数要求 \(J_a>17.5\) kg·m²，但文中只测试 ±5%。
4. 快速扭矩反例 `fail`：40/300 N·m 每 10 ms 交替时，Q6 有 49 次限幅且能量/速度误差均略差于 Q5。
5. 停止状态 `fail`：恒定 300 N·m 的 20 s 案例在 8.62 s 过零并到达 −679.66 rpm，代码仍给能量误差 0%。

完整输入、公式与数值见 `counterexamples.md`。

## 泄漏、过拟合与验证独立性

- 网络/禁区静态扫描：`pass`。代码未发现 `requests`、`urllib`、socket、URL 或 Vault 访问；`retrieval-log.json` 的 cards 为空。出现的 Vault/参考论文字符串只在禁读政策和未使用声明中。
- 参数拟合/训练验证混用：`pass`。控制律没有从附表拟合自由参数，不存在常规训练集泄漏。
- 泛化验证：`needs_review`。同一扭矩轨迹同时用于设计说明与性能评价；所谓稳健性只是该轨迹的重采样/缩放，不是独立物理验证。
- 自动 verifier 的形式独立性：`needs_review`。它用另一段标量代码复算若干公式，但 Q6 仍复写同一控制律，并以硬编码 Markdown 数字 token、PDF 大小和日志标记作为部分一致性条件；不能替代性质验证。

## 论文—代码—结果一致性

状态：`fail`。

- 正式 `metrics.json`、生成 TeX 宏和实际编译章节的主要数字一致。
- 冻结 `summary.json` 属于另一控制器管线，与正式 Q5/Q6 数字冲突。
- 冻结 PDF 缺少当前 `main.tex` 已加入的“参考文献”节；干净重编译后出现该节。
- 冻结 `verification.json` 记录的 `main.tex` 哈希不是 FROZEN 中实际源文件哈希，却仍给 overall `pass`。

## Findings 概览

| ID | 严重度 | 类别 | 简述 |
|---|---|---|---|
| AUD-001 | major | input provenance | Excel 打开前未固定原件哈希，且所谓只读提取改变 `.xls` 字节 |
| AUD-002 | major | reproduction pipeline | 两条冲突管线混入冻结包，41 个产物不能由正式入口再生 |
| AUD-003 | major | paper/code consistency | 冻结 PDF 早于 TeX 源，验证报告哈希仍错误地 pass |
| AUD-004 | major | constraint enforcement | 低扭矩下 ±30 kg·m² 限幅实现失效 |
| AUD-005 | major | stability/robustness | 速度噪声高增益、稳定域和极端工况验证缺失 |
| AUD-006 | major | stopping constraints | 无停止逻辑，可把反向旋转判为零误差 |
| AUD-007 | minor | reproducibility metadata | dirty freeze、空依赖、缓存和跨 shell 字节差异 |
| AUD-008 | suggestion | quadrature | 线性插值假设与主积分公式存在极小不一致 |

结构化 findings 与逐项修复建议见 `audit-findings.yaml` 和 `revision-plan.md`。

## 审计边界

本次只读取允许路径中的 `input`、`frozen-solution` 与 `$cumcm-a-audit` Skill；没有读取参考论文、Vault、讲评或标准答案，没有联网检索当前题目，没有调用其他阶段 Skill，也没有修改冻结版本。所有临时复现与探针目录均位于当前 audit 工作区，报告完成后清理。
