# 2013A 冻结解答独立审计报告

生成时间：2026-08-25 05:55:12 +08:00
阶段：`audit`
总体状态：`needs_review`

## 结论

冻结包身份与确定性复现均为 `pass`：62 个清单文件的大小和 SHA-256 全部匹配，树哈希匹配；在不含既有结果的新目录中以 `pwsh -NoProfile` 运行后退出码为 0，stderr 为空，19 个生成结果/图与冻结版本逐文件同哈希。

常状态三角基本图公式本身的量纲和表内算术也为 `pass`。独立复算得到中心情景的到达密度 37.5 pcu/km、拥堵密度 353.333333 pcu/km、增长速度 1.583113456 km/h、首次到达时间 5.306 min；10 行容量表、45 行问题 3 网格和 81 行联合网格均在其声明的舍入精度内匹配。

题目级有效性仍为 `needs_review`。核心原因不是复现失败，而是：两个必要视频缺失；信号脉冲虽为题面条件却未进入代码；问题 1 的容量估计器未实现；有限路段达到上游路口后的边界未约束；问题 2 的容量排序不能由 `1-p` 单独推出；所谓 1 s 数值验证复用了同一个解析速度。共记录 7 条 `major`、4 条 `minor`，无 `critical`。

| 审计层 | 状态 | 结论边界 |
|---|---|---|
| 阶段锁与路径清单 | `pass` | audit 锁、Skill、允许/禁止路径清单哈希匹配 |
| FROZEN 清单与树哈希 | `pass` | 62/62 文件匹配，无漏列、缺失或额外 payload |
| 干净目录复现 | `pass` | 当前主机上的新文件系统副本、`-NoProfile` 子进程 |
| 冻结输出字节一致 | `pass` | 19/19 个结果与图 SHA-256 匹配 |
| 常状态公式量纲与算术 | `pass` | 仅证明声明公式内的计算一致 |
| 四问案例闭合 | `needs_review` | 视频 1、2 缺失，问题 1、2 及问题 4 容量未闭合 |
| 信号/边界/因果稳健性 | `fail` | 已有可行反例推翻“均值流量足以”和严格车道排序 |
| 泄漏来源链 | `needs_review` | 未发现禁读材料内容，但检索与冻结元数据互相矛盾 |
| 过拟合/外部有效性 | `needs_review` | 无视频数据，无法执行留出验证或经验泛化审查 |
| PDF 编译与目视检查 | `needs_review` | 当前环境无 TeX 引擎 |

## 审计边界与合规

- 只读取了阶段 Skill、当前审计工作区的阶段锁文件、`input` 与 `frozen-solution`；未读取 Vault、参考论文、讲评、标准答案或其他阶段材料。
- 未联网搜索题目、题名、答案或现成解答。
- 未调用其他阶段 Skill，未跨阶段 resume。
- 未修改 `frozen-solution`。全部审计输出写在 audit 工作区；结束前再次重算 62 个文件及树哈希，状态仍为 `pass`。
- 指定的 `check_phase.py` 首次调用因系统不存在 `python` 命令而未启动，进程状态为 `fail`。随后完整读取该脚本，用 PowerShell 等价执行其 `phase == audit` 与 `frozen-solution` 存在性检查，并额外验证外层路径清单哈希；门禁结果为 `pass`。此替代过程未被伪装成 Python 成功执行。

## FROZEN 验证

- 清单：`frozen-solution/FROZEN_BLIND_V1.json`
- 版本：`blind-v1`
- 清单文件数：62
- 实际 payload 文件数（不含清单自身）：62
- 大小/逐文件 SHA-256 失败数：0
- 未列入文件数：0
- 清单列出但缺失文件数：0
- 树哈希算法：按清单顺序对 UTF-8 `path + NUL + file_sha256 + LF` 求 SHA-256
- 期望/实算树哈希：`fed823dd6b3f10cfb554040c4e6955c179861964db5e2f5af3ca4fa8ba26b94e`
- 结束后复核：`pass`

FROZEN 中记录 `git_dirty=true`。最小复制工作区没有允许用于解析该提交的仓库上下文，因此提交身份判为 `needs_review`；本次审计以逐文件哈希和树哈希为权威身份依据。

## 干净环境复现

隔离目录为 `clean-reproduction-run1`。初始仅复制 `code/`、`input/`、`paper/`，不带 `results/` 或 `figures/`；10 个源文件与冻结版本逐项同哈希。运行环境为 Windows 10.0.26200、PowerShell Core 7.6.4、.NET 10.0.10、System.Drawing.Common 10.0.0.0、`zh-CN`，未加载用户 profile，未使用网络。

执行命令：

```text
pwsh -NoProfile -File clean-reproduction-run1/code/run-all.ps1 -Workspace clean-reproduction-run1
```

运行记录：

- 开始：2026-08-25T05:46:18.8579850+08:00
- 结束：2026-08-25T05:46:20.1942149+08:00
- 用时：1333 ms
- 退出码：0
- stdout：求解 `pass`、16 项论文标记/图引用检查 `pass`、17 个声明文件双跑哈希 `pass`
- stderr：空
- 清洁运行生成：15 个 results 文件、4 个 figures 文件
- 与冻结版本外部比对：19/19 同哈希，状态 `pass`

该“干净”结论指新文件系统副本和无 profile 子进程，并非新装操作系统或容器；后一层为 `needs_review`。

## 数学、量纲与数值审查

常状态模型令

```text
k_a = q/v_f
k_b = m k_j - C/w
g = (q-C)/(k_b-k_a)
T_D = 60 D/g
```

分子单位为 pcu/h，密度差单位为 pcu/km，故 `g` 为 km/h；`D/g` 为 h，乘 60 后为 min，量纲为 `pass`。中心参数的密度次序、可行性和数值均正确。独立表格复算的最大绝对差为：容量表 `3.34e-7 min`，问题 3 网格 `7.69e-5 m`，联合网格 `8.88e-16 min`。

但上述只覆盖常状态、无限路段、已知参数的解析世界。主要失败证据如下：

1. **同均值信号反例。** 在同一参数下，用 30 s 的 3000 pcu/h 与 30 s 的 0 pcu/h 构成均值 1500 pcu/h 的周期流；3000 低于代码自身三车道基本图能力 4581.82 pcu/h。按论文声称的分段反射公式，绿灯先行时 3.260 min、红灯先行时 3.760 min 即到达 140 m，而常流结果为 5.306 min。该反例不是本案预测，而是证明均值流量与容量不足以唯一决定答案。
2. **有限路段反例。** 原路段上游边界为 240 m，但问题 3 网格有 10/45 行越界，最大 597.015 m。问题 4 已在 318.36 s 到达 140 m，CSV 仍输出 330 s、145.118692 m；这与论文“到达后边界条件改变”的限定冲突。
3. **车道排序反例。** 若三种剩余车道的基础饱和流相同、合流在瓶颈前完成且不改变放行头时距，则三种容量相等，虽然 `1-p` 分别为 0.56、0.65、0.79。因此该代理不能推出严格容量排序。
4. **验证闭环。** 1 s 推进直接使用解析程序已经算出的 `central.queue_growth_speed_km_h`；0.64 s 误差只是 `ceil(318.36/1)-318.36` 的首次越界量化误差，不是独立验证。
5. **近阈值数值反例。** 当 `q-C=0.0001 pcu/h` 时，原始增长速度为 `3.5398229e-7 km/h`，但六位小数提前舍入为 0；同一返回对象仍给出有限到达时间，内部状态不一致。

## 约束、覆盖、泄漏与过拟合

### 小问覆盖

输入清单只有两个 OLE Word 文件，视频数为 0。问题 1 没有事故阶段或 `C_1(t)` 数值，问题 2 没有 `C_1/C_2` 实证差值，问题 4 的 `C=1000 pcu/h` 是未标定工程情景。因此案例闭合为 `needs_review`；`small_question_coverage_status=pass` 应降级。

代码也没有实现论文所述的问题 1 管线：PCU 权重未被引用，三周期中位数没有用于视频容量，MAD 分段不存在，输出仅为空标注模板。即使后来放入两个视频文件，当前代码也不会产生所承诺的容量曲线。

### 泄漏与来源链

冻结文件清单和可读文本中未发现当前题目的参考论文、讲评、标准答案或现成解答；命中的禁读词只出现在禁止规则和“未读取”的声明中。三张随冻结包复制的训练卡内容是通用验证流程，不含本题数值答案。就可见 payload 内容而言为 `pass`。

但来源链不能判为 `pass`：根 `retrieval-log.json` 记录零张卡，另一个检索报告和使用记录记录三张卡，FROZEN 顶层又记录空 `knowledge_cards`；同一 FROZEN 还把依赖、种子和运行命令记为空，与实际交付冲突。由于无法仅凭互相矛盾的日志证明 solve 会话的完整访问史，泄漏 provenance 为 `needs_review`，并不等同于已发现泄漏。

### 过拟合与稳健性

当前没有视频样本，因此不存在可审查的经验拟合/留出结果；过拟合状态只能是 `needs_review`。81 点工程网格是场景枚举，不是独立验证或置信区间。它未覆盖信号相位、占空比、初始相位、支路净流、视频计数误差、PCU 权重或有限路段边界，故 `robustness_analysis_status=pass` 证据不足。

## 论文—代码一致性

- 数字标记和四张图的引用检查为 `pass`，但检查器只搜索隐藏标记、文件名和“问题 1-4”字样，不验证可见公式或论证。
- 论文声称代码可对时变输入分段积分，代码没有该实现：`fail`。
- `model-selection.md` 在 `delta_t` 已定义为小时的情况下额外乘 3600，与论文公式冲突：`fail`。
- Markdown 和 TeX 各有一处 `qquad` 缺少反斜杠：`fail`。
- 当前环境无 `xelatex`、`latexmk` 或 `tectonic`，PDF 编译和逐页目视检查为 `needs_review`。

## Findings 汇总

| ID | 严重度 | 类别 | 摘要 |
|---|---|---|---|
| AUD-001 | major | data_completeness | 视频缺失导致四问未闭合，覆盖 pass 过强 |
| AUD-002 | major | constraint_omission | 信号参数未使用，同均值反例改变到达时间 29%-39% |
| AUD-003 | major | implementation_gap | 问题 1 稳健容量估计器未实现 |
| AUD-004 | major | boundary_condition | 有限路段溢出后仍生成结果和图 |
| AUD-005 | major | causal_inference | `1-p` 不能推出严格容量排序 |
| AUD-006 | major | validation | 1 s 检查复用解析速度，非独立验证 |
| AUD-007 | minor | input_integrity | 输入身份检查只数文件，不比期望哈希/可解码性 |
| AUD-008 | major | leakage_provenance | 检索、知识卡和冻结运行元数据互相矛盾 |
| AUD-009 | minor | unit_consistency | `delta_t` 小时定义与额外 3600 因子冲突 |
| AUD-010 | minor | numerical_stability | 近 `C=q` 提前舍入导致速度为零而时间有限 |
| AUD-011 | minor | paper_consistency | `qquad` 排版错误，PDF 未编译检查 |

完整结构化证据见 [audit-findings.yaml](audit-findings.yaml)，反例见 [counterexamples.md](counterexamples.md)，复现证据见 [reproduction-report.json](reproduction-report.json)，修订顺序见 [revision-plan.md](revision-plan.md)。本审计没有修改冻结解答。
