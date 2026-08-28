# 2011A 冻结解答独立审计报告

## 审计结论

总体状态：`fail`。

冻结完整性与数值复现均为 `pass`：阶段锁有效，112 个清单文件的大小和 SHA-256 全部一致，整树哈希前后均为 `c5b0e04d2c0a233bae1dd2209bfc2a3ebd09dd8114d48b613eca15fd921e572f`；在不含旧结果的隔离副本中连续完整运行两次，52 个数值、表格和图形输出两次相同，且逐项等于冻结基线。

总体仍判为 `fail`，原因是存在 6 项 major 问题：冻结 PDF 与冻结 TeX 源不对应；传播方向基本可由采样几何解释；半变异“块金比/经验程”定义与稳定性不足；Pb 源区稳定性被现有删点规则漏判；区域背景压力测试是代数恒等且独立点 bootstrap 忽略空间相关；后续 PDE 缺少使问题适定和量纲明确的必要条件。另有 3 项 minor 和 1 项 suggestion。未发现 critical 问题。

本审计未联网、未读取参考论文或 Vault，未调用其他阶段 Skill，未修改 `frozen-solution`。

## 阶段锁与冻结校验

| 检查 | 状态 | 证据 |
|---|---|---|
| audit phase lock | `pass` | `check_phase.py` 返回 `[PASS] audit 阶段锁有效` |
| 控制文件哈希 | `pass` | `allowed-paths.json` 与 `forbidden-paths.json` 的 SHA-256 均匹配 `phase-lock.json` |
| FROZEN 逐文件校验 | `pass` | 112/112 文件存在，大小与 SHA-256 全部匹配 |
| FROZEN 整树校验 | `pass` | 按 `path + NUL + sha256 + LF` 排序拼接后重算值匹配清单 |
| 审计后复核 | `pass` | 112/112 与整树哈希再次匹配，证明冻结目录未被审计改写 |

冻结清单本身可验证，但其内部元数据为 `needs_review`：`dependencies: []` 与实际 `requirements.txt`/复现环境不一致，`solution-report.yaml` 又记载 `phase_actions.frozen: false`。

## 干净环境复现

隔离副本初始只含已校验的 `code/`、冻结 `input/` 和空 `reports/`，不含旧 `results/`、`figures/`、论文生成表或 PDF。未安装或下载依赖；使用当前离线运行时，版本与冻结说明逐项一致。

| 项目 | 状态 | 结果 |
|---|---|---|
| Python/数值库版本 | `pass` | Python 3.12.13；NumPy 2.5.2；Pandas 3.0.5；SciPy 1.18.0；scikit-learn 1.9.0；Matplotlib 3.11.1 |
| Excel 导出环境 | `pass` | Excel COM 12.0，只读 `Value2` |
| 两次完整数值运行 | `pass` | exit code 0；44.033 s；stderr 为空；52/52 两次哈希相同 |
| 与冻结数值基线比较 | `pass` | 52/52 路径、大小和 SHA-256 相同 |
| XeLaTeX 连续编译 | `pass` | 两次 exit code 0；13 页；第二次无未定义引用；仅有已声明的非致命版式/更新提示 |
| 冻结 PDF 是否由冻结源产生 | `fail` | 干净 PDF SHA-256 为 `a9859844…2f33`，冻结 PDF 为 `497aee7b…fb32`；去空白文本有 8 处语义差异 |
| 作者一致性脚本 | `pass` | 脚本返回 pass，但其检查范围无法发现上述 PDF—TeX 差异 |

完整命令、环境、stdout、stderr、退出码和比较结果见 `reproduction-report.json`。

## 输入身份

题目 `.doc` 的外层文件与冻结副本哈希一致。数据 `.xls` 的外层文件 SHA-256 为 `fab6657f…5827`，冻结副本为 `d6afcdea…0b6a`，故字节身份为 `needs_review`。两文件大小和时间戳相同，仅末端连续 5 个字节不同；Excel 12.0 只读导出的 3 张表名称、维度和逐行规范化内容完全一致。因此当前数学结果不受该差异影响，但论文所写“原始工作簿 SHA-256”并不等于当前外层原始文件，应修复来源追踪。

## 分小问审计

### Q1：空间分布与区域污染

状态：`needs_review`。

- Gaussian 模型族选择经额外压力测试为 `pass`：200 个随机平衡空间块划分中 Gaussian 200/200 次优于 IDW；连续 5 区留出中 Gaussian 仍最优。
- 精确平滑尺度为 `needs_review`：连续区域留出在五折中选择 3–4 km，而最终图使用 2 km，故 2 km 不能解释为物理传播尺度。
- 工业区首位的 i.i.d. 点 bootstrap 频率为 91.75%；按 3 km 空间块重采样后为 81.96%。总体排序仍有支持，但原不确定性偏乐观。
- 共同背景扰动的排序 100% 不变是公式决定的恒等结果，不能证明背景空间不确定性下仍稳健。

### Q2：主要原因

状态：`needs_review`。

- 功能区 Kruskal 结果和效应量数值可复算；论文已正确把工业/交通解释降级为关联线索，未伪装为具体排放因果。
- NMF 在切折前用全体样本计算 RMS 尺度，构成预处理泄漏。改为每个训练折内拟合尺度后，k=2/3/4 RMSE 仅轻微变为 0.21465/0.16769/0.13910，仍选择 k=3；因此当前分量数未被推翻，但验证实现应修复。
- 20 次 `nndsvdar` 种子扰动只证明该初始化邻域内数值稳定，不证明源谱唯一；论文对此已有 `needs_review` 限定。

### Q3：传播特征与污染源

状态：`fail`。

- 方向椭圆的 2000 次条件置换中，8 个元素没有一个各向异性比达到单侧 0.05 显著性；6/8 个元素的主轴与“仅采样几何”主轴相差不足 6°。论文“高值团通常沿西南—东北向拉伸”的证据不足。
- 代码把 0–1 km 距离箱半方差/全局方差命名为块金比，并把首次越过 0.95 的箱中点命名为经验程；没有拟合块金或变异函数模型，也未验证平稳性。仅做线性去趋势，8.5–12.5 km 就变为 1.5–3.5 km。
- Pb 被标为内部 `pass`，但删除第二高 Pb 样点 ID 6 后主峰移动 3.69 km。现有规则只删除原始最大点，漏掉更有影响的非最大点。
- As/Cr/Ni 主峰的 Gaussian 权重有效样本量只有约 4.09/6.24/5.52；Cu/Hg/Pb 报告的主峰 1 km 内没有采样点。候选坐标缺少最低支持约束和位置区间。

### Q4：模型评价与后续模型

状态：`fail`。

补充数据清单较完整，但所给对流—扩散—反应方程没有定义空间域、初始条件、边界条件、核归一化与各项单位，也没有明确 `D` 半正定、`lambda/q/c` 非负等约束。`qK` 的尺度不可分，未约束的加性状态噪声还能产生负浓度；因此不能称为已“可辨识”或适定的反演模型。

## 主要发现索引

| ID | 严重度 | 类别 | 状态 | 摘要 |
|---|---|---|---|---|
| AUD-001 | major | reproduction | `fail` | 冻结 PDF 与冻结 TeX 源不对应 |
| AUD-002 | major | spatial_inference | `fail` | 传播方向未超过采样几何零模型 |
| AUD-003 | major | variogram | `fail` | 块金比/经验程定义与去趋势敏感性有误 |
| AUD-004 | major | source_robustness | `fail` | Pb 稳定性漏判，若干峰支持不足 |
| AUD-005 | major | validation | `fail` | 背景压力测试恒等，点 bootstrap 忽略空间相关 |
| AUD-006 | major | model_specification | `fail` | PDE 缺初边值、量纲和可行性约束 |
| AUD-007 | minor | leakage | `fail` | NMF 全数据缩放泄漏，当前选择未改变 |
| AUD-008 | minor | provenance | `needs_review` | 当前外层 XLS 与冻结输入字节哈希不同 |
| AUD-009 | minor | metadata | `fail` | 冻结元数据内部矛盾 |
| AUD-010 | suggestion | validation_scope | `needs_review` | 空间折评估的是局部插值，不是带缓冲外推 |

机器可读细节见 `audit-findings.yaml`，反例见 `counterexamples.md`，只读修订建议见 `revision-plan.md`。

## 审计边界

- 未证明任何具体企业、道路或工艺是物理污染源。
- 未把哈希复现等同于数学正确性。
- 额外 bootstrap、置换和去趋势均是审计压力测试，不是新的正式答案或外部真值。
- 所有需要领域判断或新数据验证的结论保持 `needs_review`。
