# 2021A blind-v1 冻结解答独立审计报告

生成时间：2026-08-28T18:40:00.6210525+08:00
阶段：audit
总体状态：fail

总体结论：冻结包的身份、逐文件哈希、干净复现、确定性、已输出方案的精确几何约束和量纲检查均为 pass；但“尽量贴近”的模型选择被两个明确可行方案严格改进，状态为 fail。第 3 问对面板曲率/法向解释高度敏感，物理结论保持 needs_review。另有 Markdown 控制字符和焦面图截断两项论文—代码一致性缺陷。

## 1. 审计边界

- 只执行显式指定的 $cumcm-a-audit；未调用其他阶段 Skill，未跨阶段 resume。
- 只读取 allowed-paths.json 列出的 input、frozen-solution 和 audit Skill。
- 未联网搜索题目、题名、答案、现成解答或额外材料。
- 未读取 reference-vault、exam-vault、参考论文、讲评或标准答案。
- 未修改 frozen-solution。所有隔离复现和反例计算均在 audit 根目录的一次性副本中进行。
- 题面三页由冻结包内、已纳入 FROZEN 清单的页图逐页核对。

## 2. FROZEN 与阶段完整性

状态：pass

- check_phase.py 退出码 0，stdout 为 [PASS] audit 阶段锁有效。
- phase-lock 指定 phase=audit、skill=$cumcm-a-audit。
- allowed-paths.json 与 forbidden-paths.json 的实际 SHA-256 均与 phase-lock 一致。
- FROZEN_BLIND_V1.json 自身 SHA-256：
  1e68ea7a31c7932bfe9cf939e5f6f30d460d542aff9bb87e851f2672bc5417a2。
- 清单内 64/64 个文件的存在性、字节数和 SHA-256 全部为 pass；无未登记非排除文件。
- tree_sha256 按 path + NUL + file_sha256 + LF 的排序记录独立重算，实际值与清单值均为：
  cd959e705337e1eb757c9fc7192db8289503abda1cc4cdf5689ff8f026900032。
- 外层 input 的题面和四个附件共 5 个文件均与 frozen-solution/input 同哈希。
- 所有反例完成后再次复核 64 个文件和 tree_sha256，仍为 pass。
- 清单记录的 git_commit 在最小复制允许路径中没有可独立核验的 Git 元数据，因此该字段为 needs_review；未伪造提交验证。

## 3. 干净环境复现

状态：pass

隔离副本首跑前不存在 results、figures、<SOURCE_FILE_REDACTED> 或 paper/generated_numbers.tex。环境与 requirements.txt 完全一致：

| 组件 | 版本 |
|---|---|
| Python | 3.12.13 |
| NumPy | 2.5.2 |
| pandas | 3.0.5 |
| SciPy | 1.18.1 |
| Matplotlib | 3.11.1 |
| openpyxl | 3.1.5 |
| 平台 | Windows-11-10.0.26200-SP0 |

首轮命令 python code/solve.py：

- 退出码 0；
- 耗时 48.227355 s；
- stdout 末行 INTERNAL_STATUS=pass；
- stderr 只有隔离 MPLCONFIGDIR 首次建立字体缓存的提示；
- 三问摘要逐值等于冻结 summary.json。

随后命令 python code/verify.py：

- 外层退出码 0，耗时 100.736099 s；
- 内部两轮求解耗时 39.987426 s 与 39.848606 s，退出码均为 0；
- 12 个关键数值文件两轮同哈希；
- 工作簿单元格、节点编号、行程编号及论文数字检查为 pass；
- stderr 为空。

首轮共生成 21 个文件，其中 20 个非 XLSX 文件与冻结版逐字节同 SHA-256，包括全部 6 张图。<SOURCE_FILE_REDACTED> 只有 ZIP 成员 docProps/core.xml 的生成时间元数据不同；其余 11 个 ZIP 成员同哈希，三张表的值、维度、合并单元格与基本格式一致，状态为 pass。完整命令、环境和捕获输出见 reproduction-report.json。

XeLaTeX 与 latexmk 在当前环境均不可用；全部 5 个图片引用和 generated_numbers.tex 存在，但实际编译与逐页检查状态为 needs_review。

## 4. 自动判定矩阵

| 审计项 | 状态 | 主要证据 |
|---|---|---|
| 阶段锁与路径边界 | pass | check_phase、两份路径清单哈希 |
| FROZEN 逐文件与树哈希 | pass | 64/64 文件、前后两次 tree_sha256 |
| 原始输入身份 | pass | 外层 5 文件与冻结副本同哈希 |
| 干净执行复现 | pass | solve 退出码 0；20 个生成文件逐字节一致 |
| verify.py 连续复跑 | pass | 两轮退出码 0；12 个关键文件同哈希 |
| 小问覆盖 | pass | 三问均有结构化结果、论文位置和工作簿 |
| 公式量纲 | pass | 抛物面、线性应变、促动器闭合、射线交点量纲一致 |
| 问题 1 连续极小极大数值 | pass | 独立连续解与冻结值相差约 1.01e-6 m |
| 选定 0.060% 线性 QP 数值求解 | pass | KKT 站立性 L∞=8.33e-17 |
| 已输出方案精确与舍入约束 | pass | 全 6525 边、行程、闭合和六位输出 |
| 焦距/安全帽模型选择与最优性 | fail | AUD-2021A-001 的两个可行改进反例 |
| 可辨识性与多目标取舍 | needs_review | 固定焦点仍有焦距自由度，题面未唯一指定最小最大/RMS |
| 分片平面射线积分内部收敛 | pass | 分辨率与 5 种子稳定性 |
| 面板曲率下的第 3 问稳健性 | needs_review | AUD-2021A-002，基准结果相差 6.516 倍 |
| 泄漏与外部读取 | pass | 静态扫描只读本地 input；verify 仅调用自身 solver |
| 统计过拟合 | pass | 无训练/验证数据；射线结果未参与 C2 选择 |
| 论文数值—代码一致性 | pass | TeX 数值、CSV、JSON、工作簿一致 |
| Markdown 文档完整性 | fail | 29 个异常控制字符 |
| 焦面图—代码一致性 | fail | 6 m 未披露截断，且未按投影功率加权 |
| LaTeX 实际编译 | needs_review | 当前环境无 xelatex/latexmk |
| 总体审计 | fail | 两项 major、两项 minor |

## 5. 数学、量纲与约束复核

### 5.1 问题 1

径向交会方程中 \(a\) 的量纲为 1/m，\(at^2-qt-v=0\) 各项均为 m；稳定根形式正确。独立以中心负峰和内部正峰平衡求得：

- \(f=140.322341696\) m；
- 内部峰半径 106.312168 m；
- 最大绝对径向位移 0.335934561 m。

冻结结果的微米级差异来自 100001 点网格，不影响现有六位以前的工程结论，但第六位焦距不宜写成严格解析精度。

固定焦点只把理想面缩减为一参数族，并不自动唯一决定焦距。面积 RMS 准则可给出 \(f=140.427871099\) m，且最大径向位移 0.455231 m 仍小于 0.6 m。因此“最大位移最小”是建模偏好，不是由题面约束唯一推出；状态为 needs_review。

### 5.2 问题 2

线性应变系数的量纲为 1/m，乘位移后无量纲；促动器二次闭合方程各项为 m²。冻结 0.060% 子问题是严格凸二次投影：

- 998 个线性约束活动；
- KKT 站立性 L2=4.58e-16、L∞=8.33e-17；
- 没有位移边界活动；
- 所以 SLSQP 对该线性代理的求解状态为 pass。

对最终坐标检查全部 6525 条边：

- 2165 条受影响边最大绝对变化 0.000657281989；
- 4360 条两端不活动边变化为 0；
- 促动器行程范围 [-0.241391559, 0.180240182] m；
- 活动促动器判别式最小 2.441，另一根绝对值至少 3.047 m；
- 沿 101 个连续形变步无根切换；
- 闭合残差最大 3.06e-14 m；
- 六位舍入后边长、行程、闭合仍为 pass。

缺陷不在可行性，而在选择的焦距和人为安全帽。详见 AUD-2021A-001 与 counterexamples.md。

### 5.3 问题 3

在“每块面板为平面三角镜”这一条件模型内：

- 反射定律、馈源平面交点和投影功率权重公式量纲正确；
- 连续理想抛物面焦面横向误差约 2.02e-13 m；
- 5 个 Sobol 种子、每面板 \(2^{15}\) 点时，调整面接收比范围
  0.01039049～0.01039577，基准面 0.00810442～0.00811094；
- 相对提高范围 28.163%～28.246%，数值稳定性为 pass。

但是球面逐点法向反例把基准接收比提高到 5.28245%，说明面板几何模型不是小扰动。当前第 3 问只能解释为“分片平面条件下的数值结果”；外部和相对物理有效性均为 needs_review。

## 6. 反例、简化模型与敏感性

四组核心试验见 counterexamples.md：

1. 同焦距把线性帽从 0.060% 调至 0.063%，在精确与舍入约束均 pass 时把 RMS 降至 0.078280 m。
2. 同 0.060% 帽令 \(f=F+0.4\) m，把 RMS 降至 0.067365 m，改善 20.33%，全部约束仍 pass。
3. 连续球面逐点法向给出 5.28245% 基准接收比，是冻结值 6.516 倍。
4. 一参数缩放 \(d_i=\lambda d_i^\ast\) 的 RMS 为 0.086058 m，仅比 692 变量 C2 差约 1.78%，同时最大误差和行程更小；应作为简单可行基线。

活动节点离口径边界最小仍有 0.0310 m，毫米级坐标扰动不会改变 692 节点集合，状态为 pass。

## 7. 泄漏、过拟合与证据隔离

状态：pass

- solve.py 的文件发现与哈希遍历均限制在 ROOT/input；未发现 requests、urllib、socket、URL 或 Vault 路径。
- verify.py 唯一 subprocess 调用为当前 ROOT/code/solve.py。
- retrieval 记录只显示三张 provisional_training 验证类卡；没有参考论文、标准答案或当前题解。
- 模型选择使用几何约束和面形误差，第 3 问射线结果未用于选择 C2，不存在统计训练集上的评价泄漏。
- 0.060% 安全帽选择缺陷属于目标/鲁棒性未定义和模型选择不足，不是统计过拟合。

## 8. 论文—代码—图表一致性

数值一致性状态为 pass：TeX 宏、JSON、CSV、<SOURCE_FILE_REDACTED> 和图像都能由隔离首跑复现。

文档完整性状态为 fail：

- paper/paper.md 有 26 个异常控制字符，problem-analysis.md 有 3 个；
- beta、boldsymbol、varepsilon、rho、rm 等转义被破坏；
- verify.py 只搜索若干数字字符串，未覆盖可渲染性。

图表含义状态为 fail：

<SOURCE_FILE_REDACTED> 只绘制半径 6 m 内的条件样本；
- 该窗口仅保留调整面约 80.61%、基准面约 62.39% 的投影功率；
- 图注没有披露截断，且 hexbin 未按投影功率加权；
- summary.json 的接收比计算没有使用该 6 m 筛选，因此数值本身不受图形缺陷影响。

## 9. Findings 与修订优先级

结构化 findings 见 audit-findings.yaml：

- AUD-2021A-001，major，优化与焦距选择被可行方案严格改进；
- AUD-2021A-002，major，面板几何解释使第 3 问高度不稳健；
- AUD-2021A-003，minor，Markdown 控制字符未被验证器发现；
- AUD-2021A-004，minor，焦面图未披露截断且权重不一致。

证据化下一版方案见 revision-plan.md。最高优先级是联合优化 \(f,d\) 与重建/包络面板曲率模型；随后补简单基线、修复文档验证和焦面图。冻结版本未被改写。

## 10. 交付物

- audit-report.md：本报告；
- audit-findings.yaml：结构化 findings；
- reproduction-report.json：清单、环境、命令、stdout/stderr、退出码与输出比较；
- counterexamples.md：反例、极端检查和简单模型；
- revision-plan.md：只读修订建议。
