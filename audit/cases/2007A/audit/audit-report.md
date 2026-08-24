# 2007A 冻结解答独立审计报告

## 审计结论

总体判断：fail。

数值代码可以在全新工作副本中确定性复现，23 个数值/图形输出与现有运行清单逐项同哈希；但完整冻结完整性不成立，且发现会改变长期预测的 TFR 定义错误。因此，本次审计不能认证当前 frozen-solution 为“完整、已冻结且数学正确”的版本。

最优先的阻断项是 AUD-001：reproducibility.yaml 所记 paper/<SOURCE_FILE_REDACTED> 哈希与当前文件不一致，当前解答树也没有覆盖全部提交材料的 FROZEN 清单。按阶段 Skill，这一项使冻结验证为 fail。后续数学实验是在发现该未覆盖哈希前完成，其结果仅作为修订证据，不改变冻结完整性结论。

## 范围与边界

- 仅执行 $cumcm-a-audit，没有调用其他阶段 Skill，也没有跨阶段 resume。
- 仅读取 allowed-paths.json 允许的 input、frozen-solution 和 audit Skill。
- 未读取 reference-vault、exam-vault、参考论文、讲评或标准答案。
- 未联网搜索题目、题名、答案或现成解答；代码中也未发现网络客户端调用，knowledge 为空，retrieval-log.json 的 cards 为空。
- 未修改 frozen-solution。审计输出只写入 audit 工作区根目录；隔离复现副本只用于审计运行。

## 阶段锁与冻结完整性

| 检查 | 判断 | 证据 |
|---|---|---|
| audit 阶段锁 | pass | check_phase.py 输出 [PASS] audit 阶段锁有效 |
| allowed-paths.json 哈希 | pass | 期望值与实算均为 5fee53eb91238a4f55d4e56e3424d9d96f4ba0e1ccd75933f96b8a4012d58a15 |
| forbidden-paths.json 哈希 | pass | 期望值与实算均为 f075bf741aa5e0dc4527893642bc07eb4bc70edc84733fa9b1801ed5ccaa0b47 |
| results/run_manifest.json 的 27 条记录 | pass | 路径均位于 frozen-solution；无重复、无缺失、已有字节数均匹配、SHA-256 全匹配 |
| 完整 FROZEN 清单覆盖 | fail | 冻结树观察到 176 个文件，运行清单只覆盖 27 个；论文、其余代码和解答说明未覆盖 |
| paper/<SOURCE_FILE_REDACTED> 记录哈希 | fail | 记录 50603cae…7ca2；当前文件连续两次实算 fe8ef5b1…7bbc，字节数均为 901123 |
| 内部冻结状态 | fail | solution-report.yaml 仍为 frozen: false |

审计开始时观察到的整树摘要为 e04eb99928c49fce9cefb0c1cb68bf69ecfc0dca921a0753b637f9ee14b7079a。算法是：按相对路径排序，对每个文件形成 relative_path、字节数、文件 SHA-256 的制表符分隔行，以 LF 拼接后再做 SHA-256。该摘要只是本次审计的观察记录，不是预先存在的冻结证明。

## 干净环境复现

干净复现判断：pass；完整审计判断仍为 fail。

复现使用全新目录，只复制 convert_inputs.ps1、solve_population.py、原始题面 DOC 和附件 RAR；运行前不存在 _work、results 或 figures。没有复制旧转换结果、旧数值输出或旧图表。

环境：

- Windows 11 10.0.26200
- PowerShell 7.6.4；转换子进程使用 powershell.exe
- Python 3.12.13，使用 python -I，isolated=1、no_user_site=1
- NumPy 2.5.2、Pandas 3.0.5、SciPy 1.18.0、Matplotlib 3.11.1
- UnRAR 7.13 x64

结果：

| 运行 | 转换 | 求解 | 关键结果 | 输出哈希 |
|---|---|---|---|---|
| clean-1，PYTHONHASHSEED=0，单线程 | exit 0，6.588 s | exit 0，24.139 s | 2005 基期 1,323,946,941.722；中情景峰值 2025、1,436,415,462.311；validation=needs_review | 23/23 与冻结清单相同 |
| clean-2，PYTHONHASHSEED=8675309，双线程 | exit 0，6.739 s | exit 0，5.031 s | 与 clean-1 完全相同 | 23/23 与 frozen、clean-1 相同 |

两次独立转换的核心 CSV 均为 59,924 字节，SHA-256 737098fc1386e85809654116aaf6ba272fd247fd40c6bb33bf091ce0bfd6ea14。Office 生成的 PDF、HTML、XLSX 含非确定性元数据，哈希变化；它们不被数值代码读取。第二次求解改变哈希种子和数值库线程数后，全部 23 个输出仍同哈希，确定性检查为 pass。

首次创建 clean-1 的命令使用了本机 New-Item 不支持的 LiteralPath 参数，产生非终止错误且没有创建任何目录或文件；该尝试明确记录为 fail 并排除。随后使用显式 Path、ErrorActionPreference=Stop 重新创建，复制文件哈希全部为 pass。

详细命令、时间、退出码、stdout/stderr 捕获摘要与日志哈希见 reproduction-report.json。

## 主要数学与验证发现

### 1. 全国 TFR 汇总公式错误（AUD-002，major）

代码先计算三个地区各自的 TFR，再按地区 15--49 岁女性总量加权。标准全国时期 TFR 应在每一个年龄上先用该年龄的女性暴露量汇总全国 ASFR，再对年龄求和。当前中情景被自定义指标缩放到 1.8，但标准 TFR 只有 1.766787。

按标准定义重新缩放后，中情景 2050 人口上升 1.1205%，2100 上升 4.2803%；高情景峰值由 2039 推迟到 2041。该差异足以改变论文的长期数字与情景解释，故不是仅有命名问题。

### 2. 单一留出年不足以支持模型选择 pass（AUD-003，major）

原解答只做 2005 留出。按同一代码做滚动起点验证，2004 留出由更简单的无城市化队列模型 M1 胜出；M2 只在 2003 和 2005 胜出。更重要的是，在 2005 单步留出中，M1 与 M2 的全国年龄—性别、年龄段和性别比指标完全相同，M2 只改善地区份额。因此该留出不能验证“迁移通过地区生育差异改变长期全国人口”的机制。

### 3. 单因素敏感性漏掉联合极端（AUD-004，major）

在论文已经采用的参数范围内做 54 个组合，最大峰值达到 15.538 亿，2050 为 15.442 亿，2100 为 14.374 亿；有 8 个组合超过 15 亿。原单因素高情景峰值只有 14.981 亿。当前敏感性图低估了联合条件包络。

### 4. 样本暴露量分母不一致（AUD-005，minor）

年龄—性别比例以地区总人口为分母，代码却用性别专属样本容量直接相乘。2005 城市 20 岁男性示例的代码暴露量是 16,267.985，分母一致的值是 32,464.548。修正后长期总量变化较小，但收缩强度和死亡曲线确实改变。

### 5. 其他一致性问题

- AUD-006：异常年识别在 2005 留出前使用了全部 2001--2005 数据，存在形式上的预处理泄漏；本数据上的异常结论未改变。
- AUD-007：run_manifest 的 pass 只跟随内部数学不变量，而同一运行的 overall validation 是 needs_review、contextual validation 是 fail；执行成功与科学正确性混淆。
- AUD-008：paper/paper.md 有 18 个 C0 控制字符，破坏 11 行公式；现有一致性检查没有发现。
- AUD-009：trajectory 的 birth_sex_ratio 用育龄女性权重而不是实际出生数汇总；2005 年少报约 0.494 个男婴/100女婴。状态递推本身按地区分配正确。

## 已通过的检查

以下 pass 只代表对应范围，不扩展为数学正确性：

- 阶段锁和路径控制文件哈希。
- 现有运行清单列出的 27 条记录。
- 原始题面、RAR、核心 CSV 和求解器哈希。
- 两次隔离转换与求解的退出码。
- 三次（冻结记录、clean-1、clean-2）23 个数值/图形输出的逐文件哈希一致。
- 固定实现下的非负性、人口恒等式、内部迁移守恒、情景次序和零生育边界。
- 冻结论文中主要硬编码数值与生成结果在当前实现下相符。
- 本地证据未显示联网、外部参考或其他阶段材料泄漏。

## 不能通过的声明

- 不能声明完整冻结版本已验证：fail。
- 不能声明现有 TFR=1.8 情景符合标准 TFR 定义：fail。
- 不能把 manifest pass 解读为数学正确：fail。
- 不能把 M2 的模型选择视为跨年份稳定：needs_review。
- 不能把单因素敏感性图当作联合不确定性包络：needs_review。
- 不能把老龄化绝对量视为已验证：原解答自身与附件 1 的 2020 年 60+/65+ 检查分别偏差 28.70%/33.48%，均为 fail。

## 最终判定

当前冻结解答的“数值可复现性”为 pass；“完整冻结完整性”为 fail；“数学与科学有效性”为 fail；“模型选择与长期稳健性”为 needs_review；综合判断为 fail。

在 AUD-001 和 AUD-002 修复并重新冻结前，不应进入依赖该冻结版本身份或长期数值结论的后续流程。修订顺序和验收门槛见 revision-plan.md；全部反例和重算细节见 counterexamples.md。
