# 2015A 冻结解答独立审计报告

- 阶段：`audit`
- 审计对象：`frozen-solution` / `blind-v1`
- 总体状态：`fail`
- 冻结身份与哈希：`pass`
- 隔离执行：`pass`
- 完整冻结产物等价复现：`fail`
- 外部地点真实性：`needs_review`

## 结论

冻结身份可信，问 1—3 的代码可以在隔离副本中确定性运行，当前候选也能由独立前向公式回算；这些层面均为 `pass`。但是，审计发现 4 项 `major`：问 4 单应矩阵公式方向和量纲不一致；舍入噪声模型与实际残差不相容；日期—地点联合不确定性和参数漂移被显著低报；所谓末 7 点留出在“选型”叙述中发生用途冲突且基线过弱。因此冻结解答不能获得整体 `pass`。

本审计未读取参考论文、讲评、标准答案、Vault 或附件链接，也未联网检索题目或现成解答。没有修改 `frozen-solution`；审计结束时 78/78 个清单文件及树哈希仍与 FROZEN 清单一致。

## 阶段、边界与冻结验证

| 检查 | 状态 | 证据 |
|---|---|---|
| audit phase lock | `pass` | `phase=audit` 且存在 `frozen-solution` |
| 原 `check_phase.py` 字面执行 | `needs_review` | 系统无可用 Python；失效虚拟环境指向不存在的 Python 3.11 |
| 脚本确定条件的 PowerShell 等价复核 | `pass` | 输出 `[PASS] audit 阶段锁有效` |
| `allowed-paths.json` 哈希 | `pass` | 与 phase lock 中 `b9e4...81db` 相同 |
| `forbidden-paths.json` 哈希 | `pass` | 与 phase lock 中 `f075...0b47` 相同 |
| FROZEN 文件集合 | `pass` | 78 个清单条目，实际也是 78 个；无漏列、多列、重复或越界路径 |
| 逐文件大小与 SHA-256 | `pass` | 78/78 一致 |
| 树哈希 | `pass` | 独立按 `path + NUL + sha256 + LF` 重算为 `e2280802035f6194a85fb6f2c7d42a8f16ebab9313748a94599edcc9caa90a2f` |
| 审计后冻结复核 | `pass` | 78/78 与树哈希仍一致 |

## 隔离复现

在 audit 工作区内建立 `_audit-repro-2`，只复制运行所需的 22 个源文件；复制哈希为 `pass`，且运行前不存在 `results`、`figures` 和 `paper/generated-values.tex`。

环境为 PowerShell 7.6.4、.NET 10.0.10、Excel COM 12.0、System.Drawing.Common 10.0。Python 与 LaTeX 引擎缺失。

| 命令 | 退出码 | 状态 |
|---|---:|---|
| `run_all.ps1 -SensitivityReplicates 100` | 0 | `pass`，报告 50.1 s、8 张图 |
| `validate_outputs.ps1` | 0 | `pass`，32 项中 31 `pass`、外部真值 1 `needs_review` |
| `check_paper_consistency.ps1` | 0 | `pass`，LaTeX 编译 `needs_review` |
| `verify_all.ps1 -Rerun -SensitivityReplicates 100` | 0 | `pass`，18/18 确定性产物复跑哈希一致 |

与冻结输出逐项比较时，38 项中 35 项 SHA-256 完全一致；`run_metadata.json` 因时间戳和耗时不同为预期的 `needs_review`；另有两项确定性检查表不一致，故“完整冻结产物等价复现”为 `fail`：

- 冻结 `<SOURCE_FILE_REDACTED>` 记录 `paper/main.tex=20411` 字节，而最终冻结文件和干净副本均为 20740 字节。
- 冻结 `<SOURCE_FILE_REDACTED>` 记录 257 对花括号且没有 `thebibliography` 检查；对最终 TeX 重跑得到 264 对，并新增 `thebibliography` 环境检查。

这说明两份检查表在最终 TeX 更新前生成，随后未重跑。

## 数学、量纲与约束

问 1 的太阳时角、东—北—天顶向量、`L=H cot(alpha)` 及单位关系在太阳位于地平线上方时为 `pass`。问 2/3 的复数旋转—缩放最小二乘公式与 C# 实现一致，当前候选的独立前向 RMSE 回算也为 `pass`。外部地点是否真实仍为 `needs_review`。

问 4 方法为 `fail`。论文先定义 `G` 把像素坐标变为米制地面坐标，随后却在目标函数中计算 `p_i - G[Hq_E,Hq_N,1]^T`：它把米制地面点送入像素到地面的矩阵，并把结果与像素点相减，域、值域和单位均不一致，也没有齐次归一化。与此同时，`G` 被当作自由优化变量却未在目标中加入控制点约束。缺失视频只能解释数值结果为 `needs_review`，不能让错误的方法公式获得 `method_status: pass`。

边界实现还缺少若干物理/数值约束：手性变量的 YAML 域写成连续区间 `[-1,1]` 而代码只接受 `{−1,+1}`；零影端数据会得到 `H=0`、RMSE=0 的非物理解；重复时刻令线性基线斜率为 NaN；太阳在地平线下时 `SolarAt` 把所有真实负高度统一报告为 −90°。当前输入避开这些边界，因此属于 `minor`，但自动检查并不健壮。

## 误差模型、稳健性与可辨识性

舍入扰动不能代表当前模型的全部误差。每轴 `±0.00005 m` 时，单点二维舍入误差上界是 0.07071 mm；附件 1 和附件 3 最佳 RMSE 分别为 0.346 mm 和 0.281 mm，是该上界的 4.90 倍和 3.98 倍。两者 x 残差的一阶相关分别约 0.927 和 0.923，显示明显系统结构，而非独立舍入噪声。只向已经带系统残差的数据再加微小舍入扰动，会严重低估模型误差、时间误差和异常点影响。

冻结结果自身已经暴露联合不确定性：

- 附件 2 的日序 202/203 和 143/144/145 跳变对应约 15.9—16.8 km 的地点差异。
- 附件 3 的日序 323/324 跳变对应约 39.3 km。
- 前 14 点拟合到全 21 点重拟合，附件 1 地点移动约 14.10 km，附件 3 移动约 7.83 km；附件 1 从前 8 点到全量的漂移约 19.99 km。
- 单点 x 坐标增加 1 mm 后，完整 365 日重搜使附件 2 主分支移动约 63.8 km，附件 3 移动约 39.3 km。

因此论文以 0.001° 精度列出地点并概括“纬度较稳”属于过度精确。应报告日期—地点的联合候选簇、分支条件下区间、切分稳定性和时钟不确定性，而不是只给边际分位数或模态日。

## 留出、泄漏与过拟合

留出用途存在直接矛盾：摘要和模型选择结论说用末 7 点“选择”完整模型；其他章节又说末 7 点只在选型完成后评价。代码在算出留出结果后按模型名称硬编码 `selected_for_main_results=pass`，没有可审计的训练集选型准则。一个集合不能同时承担选型和无偏最终评价。

留出表现也不足以支持地点稳定：附件 1、3 的物理模型留出 RMSE 分别是训练 RMSE 的 10.65 倍和 10.06 倍。审计加入的三次坐标多项式在附件 1、3 的末 7 点 RMSE 为 0.683 mm、0.802 mm，反而优于物理模型的 1.040 mm、0.911 mm。多项式不能回答地点，但它构成反例：短弧外推小误差并非地理参数正确性的证据，且只比较线性基线会夸大优势。

## 论文—代码—证据一致性

除两份滞后检查表外，还有以下 `minor` 不一致：

- 论文称“32 项检查均为 pass”，实际 `<SOURCE_FILE_REDACTED>` 为 31 `pass` 加 1 `needs_review`。
- `paper.md` 中 `theta` 的反斜线变成 TAB，`rho` 的反斜线变成裸 CR；现有一致性脚本未检查控制字符或公式语义。
- `q3_*<SOURCE_FILE_REDACTED>` 的 `rank` 先按手性分支排序，再按误差排序；附件 2 的全局最小 RMSE 实为 `rank=3` 的镜像分支，故 `rank` 不是全局误差名次。
- FROZEN 元数据把 dependencies、random_seed、run_command、knowledge_cards 留空，但实际运行需要 Excel/System.Drawing、使用 seed 2015 和 3 张训练卡；根 `retrieval-log.json` 又记录 cards 为空。最终 TeX 还有未在来源日志中登记的 NOAA URL 和教材条目，因此引用来源只能判为 `needs_review`，不能据此认定发生或未发生泄漏。

静态扫描未发现代码中的网络调用、Vault 路径读取或外部答案读取，状态为 `pass`；引用来源的人工追溯保持 `needs_review`。

## Findings 汇总

| ID | 严重度 | 类别 | 摘要 |
|---|---|---|---|
| AUD-MATH-001 | major | mathematics_units | 问 4 单应矩阵方向、单位和齐次处理错误 |
| AUD-STAT-002 | major | uncertainty | 舍入噪声与实际残差不相容，敏感性低估误差 |
| AUD-ID-003 | major | identifiability | 日期—地点联合跳变和分段参数漂移被低报 |
| AUD-VAL-004 | major | validation_leakage | 留出同时被叙述为选型与评价，且弱基线不能支撑地理结论 |
| AUD-REP-005 | minor | reproducibility | 两份冻结检查表滞后，文档命令不能完整重建验证产物 |
| AUD-PAPER-006 | minor | paper_code_consistency | 状态计数、Markdown 控制字符与 rank 语义不一致 |
| AUD-DATA-007 | minor | data_validation | 空单元格在检查前被强制转换为 0 |
| AUD-CONSTRAINT-008 | minor | constraints_numerics | 手性、正杆高、重复时刻和夜间高度边界未正确约束 |
| AUD-PROV-009 | suggestion | provenance_leakage | FROZEN、训练卡和引用来源元数据不一致 |
| AUD-VERIFY-010 | suggestion | validation_coverage | 输入哈希自生成自校验，稠密交叉检查漏掉镜像分支 |

详细结构化记录见 `audit-findings.yaml`，反例见 `counterexamples.md`，修订优先级见 `revision-plan.md`。

## 审计运行副作用说明

对外层 `input/data/*.xls` 进行 Excel COM “只读”检查时，Office 仍更新了复合文档内部 FILETIME。与冻结副本相比只有偏移 28780—28784 的 5 个 OLE 元数据字节变化，内部时间从 09:31:01 变为 11:09:17；文件长度、文件系统时间和工作表数据流未变。状态为 `fail`，因为这违反只读期望。为避免第二次未授权写入，本审计没有用冻结副本覆盖外层 input。`frozen-solution` 本身仍为 `pass` 且完全未变。

另有一个错误层级的临时副本 `_audit-repro`；删除被执行策略拒绝，未绕过。实际复现使用 `_audit-repro-2`。两者均在 audit 工作区内，不属于冻结版本。

## 审计局限

- 未使用外部地理真值，地点正确性为 `needs_review`。
- 未读取 NOAA 页面、教材或任何参考论文，未验证引用与高精度星历误差。
- 无 LaTeX 引擎，PDF 编译为 `needs_review`；仅检查源文件和重跑后的结构检查。
- 附件 4 视频缺失，视频数值结果为 `needs_review`；问 4 公式错误则可由量纲独立判为 `fail`。
