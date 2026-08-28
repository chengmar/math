# 2010A 冻结解答独立审计报告

## 审计结论

总体状态：`needs_review`。

核心几何公式、单位换算、数值积分、空满边界和主数值结果均可复现；未发现需要把主积分公式直接判为错误的 `critical` 问题。审计确认 5 条 `major`、3 条 `minor` 和 1 条 `suggestion`。主要风险来自验证集复用、小罐姿态后系统残差、流量/几何混淆、冻结包混入两代结果，以及文档化一键命令在 Windows PowerShell 5.1 下失败。

因此，不建议把当前冻结包判为完全 `pass`，也不能在不复核探针/仪表与清理产物的情况下直接采用其全部罐容表。

## 范围与边界

- 仅执行 `$cumcm-a-audit`；未调用其他阶段 Skill，未跨阶段 resume。
- 只读取当前最小工作区的 `input`、`frozen-solution`、阶段控制文件和 audit Skill。
- 未联网搜索题目、题名、答案或现成解答；未读取参考论文、reference-vault 或 exam-vault。
- 未修改 `frozen-solution`。复现和诊断均在 audit 根目录下的新工作副本中进行。

## FROZEN 与输入完整性

| 检查 | 状态 | 证据 |
| --- | --- | --- |
| audit phase lock | `pass` | Skill 检查脚本输出 `[PASS] audit 阶段锁有效` |
| allowed/forbidden 控制哈希 | `pass` | 两个实际 SHA-256 与 `phase-lock.json` 完全一致 |
| FROZEN 逐文件大小与 SHA-256 | `pass` | 135/135 通过，0 缺失，0 不匹配 |
| 外部 `input` 与冻结副本 | `pass` | 3/3 SHA-256 完全一致 |
| 目录闭包 | `needs_review` | 清单外有 FROZEN 文件本身及 5 个可执行 `__pycache__/*.pyc` |
| 聚合 `tree_sha256` 独立复算 | `needs_review` | 清单未定义规范化算法；未伪报复算成功 |

逐文件完整性足以继续只复制清单内源文件做复现；未把未列入清单的 `.pyc` 带入干净副本。

## 干净复现结果

环境与冻结说明一致：Windows 11、Python 3.12.13、NumPy 2.5.2、Pandas 3.0.5、SciPy 1.18.0、Matplotlib 3.11.1、Excel COM 12.0；PowerShell Core 为 7.6.4。

1. 按文档逐字执行 `powershell -File code/run_all.ps1`：`fail`，退出码 1。Windows PowerShell 5.1 不识别 `extract_inputs.ps1:84` 的 `-Encoding utf8NoBOM`。
2. 改用 `pwsh -NoProfile -File code/run_all.ps1`：`pass`，退出码 0；提取、求解、论文渲染、验证和总流水线均输出 PASS。
3. 两遍 XeLaTeX：均 `pass`，生成 14 页、560419 字节 PDF。PDF 因 CreationDate 元数据而字节哈希不同，但冻结版和复现版抽取文本逐字相同，文本 SHA-256 均为 `71d3cf0cdba89137fa0250029519cd1e8cdc63c8b960c3edfd15f04ec756f8b4`。
4. 首次干净主流水线生成的 27 个核心输出中，26 个与冻结哈希完全相同；`verification.json` 因干净运行仅生成 5 张 PNG、无预存 PDF，而冻结目录含 11 张 PNG 和预存 PDF，所以内容不同。
5. 真正使用自适应积分的旧 `code/verify.py` 在干净输出上 `fail`：它要求当前流水线不生成的旧 `run_manifest.json`，并面向旧文件名/schema。

标准 venv 无法继承宿主 venv 的 SciPy；本次使用新解释器并显式指向已核版本的宿主 site-packages。数值版本匹配，但环境不是从冻结依赖包密闭重建，状态为 `needs_review`。

## 数学与数值检查

以下检查为 `pass`：

- 独立 SciPy 自适应积分与生产积分：小罐最大差 (2.59\times10^{-9}) L，实际罐最大差 (2.33\times10^{-4}) L。
- 实际罐解析容量为 64664.448786 L，与代码一致。
- 报告的物理空/满读数处体积为 0/总容量；扩展高度网格上单调、有界。
- (V(h,\alpha,\beta)=V(h,\alpha,-\beta)) 精确成立，横偏符号不可辨识结论正确。
- 在更宽的 alpha/beta 边界内做 12 组多起点，全部收敛到同一内部最优；未见局部极小或数值不稳定。
- 复合 Gauss 加密最大差约 0.000843 L，远小于测量和模型误差。

论文球冠公式的数值实现正确，但文字写成 (R_s=(R^2+1^2)/2)，分母漏写冠深 (d)，量纲不闭合；详见 AUD-008。

## 主要发现

| ID | 严重度 | 状态 | 摘要 |
| --- | --- | --- | --- |
| AUD-001 | major | `fail` | 第二段和补油参与选模后又被称为独立验证，存在选择泄漏 |
| AUD-002 | major | `needs_review` | 小罐倾斜残差强结构化；合理零点反例可改表约 94 L |
| AUD-003 | major | `fail` | 冻结包混入两代代码/表/图/清单，内部 36 个哈希有 10 个不符 |
| AUD-004 | major | `fail` | 文档化 `powershell` 一键命令失败；需明确 `pwsh` 与依赖锁 |
| AUD-005 | major | `needs_review` | beta 与流量比例、尺寸公差混淆；0.1% 比例扰动即可改表约 29 L |
| AUD-006 | minor | `needs_review` | 差分残差显著负相关，常规线性化区间缺少相符误差模型 |
| AUD-007 | minor | `fail` | 正式验证器复用生产 `model.py`；独立验证器过期且未接入 |
| AUD-008 | minor | `fail` | 球冠母球半径的论文写法量纲不闭合，代码数值正确 |
| AUD-009 | suggestion | `needs_review` | 0.01 L 表格小数远超约 27–28 L 级模型/零点不确定性 |

完整证据、受影响文件与修订建议见 `audit-findings.yaml`。

## 关键反例结论

- 小罐：增加姿态零点并重新识别有效横半轴，倾斜绝对量 RMSE 从 27.314 L 降至 8.497 L，主表最大改变 94.285 L。该结果证明当前表对探针机理敏感，但仍需人工决定物理模型。
- 实际罐：流量比例 -0.1%/+0.1% 时，beta 从 4.253 deg 变为 5.040/3.285 deg，表最大改变约 29.3 L，而 RMSE 几乎不变。
- 几何：圆柱长 +/-10 mm 使表变化约 54 L；半径 +/-5 mm 使表变化约 340–355 L。没有尺寸公差就不能把单值表解释为工程置信结果。
- 残差：两段一阶相关为 -0.433/-0.447，说明增量误差并非独立；累计量目标给出接近但不完全相同的角度，表最大差 3.740 L。
- 更简单的仅纵倾模型在小增量和大额补油上不能同时达到 M2 的效果；未发现“同样有效”的更简单实际罐主模型。但 M2 的独立验证和工程稳健性仍未成立。

## 最终判定

- FROZEN 清单内文件完整性：`pass`。
- 兼容环境下的主数值复现：`pass`。
- 核心几何与数值积分：`pass`。
- 文档化默认复现命令：`fail`。
- 冻结产物唯一性与内部 provenance：`fail`。
- 小罐变位表与实际罐工程不确定性：`needs_review`。
- “独立验证”结论：`fail`。
- 总体：`needs_review`。

建议先按 `revision-plan.md` 的 P0 清理权威产物和复现链，再处理验证设计与测量不确定性；在此之前不要修改或覆盖当前冻结版本。
