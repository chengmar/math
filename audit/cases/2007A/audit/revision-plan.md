# 2007A 冻结解答修订计划

本计划只给出证据化修订建议，没有修改 frozen-solution。由于 AUD-001 为 critical，任何数学修订都应在新的可写 solve 工作区完成，完成后重新冻结；不得直接改当前冻结目录。

## P0：重建可信冻结链

关联 finding：AUD-001。

1. 由人工确认哪一个 paper/<SOURCE_FILE_REDACTED> 是权威提交文件，并保留其生成日志、源文件和编译环境。
2. 对最终提交树生成独立 FROZEN 清单，至少覆盖：
   - 题面与附件；
   - 全部代码与复现脚本；
   - 解答说明 YAML/Markdown；
   - 数值结果和图；
   - main.tex、paper.md、<SOURCE_FILE_REDACTED>；
   - 一致性与复现报告。
3. 清单每条记录包含相对路径、字节数和 SHA-256，并验证路径不能逃出冻结根。
4. 让 solution-report.yaml 的 frozen 状态、reproducibility.yaml 的 PDF 记录和 FROZEN 清单由同一次原子化冻结动作生成。
5. 冻结后只读挂载或复制到新的 audit 工作区，从阶段检查开始重新审计。

验收标准：

- 完整树的 expected_paths 与 observed_paths 完全相同：pass。
- 每个文件字节数和 SHA-256 匹配：pass。
- paper/<SOURCE_FILE_REDACTED> 的清单、reproducibility.yaml 和实算哈希三者一致：pass。
- frozen=true 且 phase lock 指向 audit：pass。
- 任一缺失、额外或哈希不符：fail 并停止审计。

## P1：修正 TFR 定义并重新生成核心结果

关联 finding：AUD-002。

1. 新增标准全国时期 TFR：

\[
\mathrm{TFR}_t
=\sum_{a=15}^{49}
\frac{\sum_r n_{r,F,a,t}f_{r,a,t}}
{\sum_r n_{r,F,a,t}}.
\]

2. 明确中情景的政策语义：
   - 若 1.8 只是 2005 基期目标，所有表图必须写“初始标准 TFR=1.8”；
   - 若 1.8 表示未来约 30 年保持，则每年或每个政策阶段重新缩放全国 ASFR，并写清 2030/2035 之后规则。
3. 低、中、高情景全部按同一标准定义校准。
4. 重新校准 2020 城镇化率和 2010 总量尺度；重新生成 projection、key results、sensitivity、图表、论文数字和清单。
5. 同时保留地区 TFR 与全国标准 TFR，避免再用含糊的 effective TFR 替代标准量。

验收标准：

- 独立按年龄暴露重算的 2005 全国 TFR 与目标误差小于 \(10^{-12}\)：pass。
- 两地区错位年龄结构的单元测试能区分旧公式与标准公式：pass。
- 论文、CSV、JSON 中所有 TFR 名称和定义一致：pass。
- 全部关键结果由修正代码重新生成，不手工改表：pass。

## P1：修正暴露量和汇总指标

关联 finding：AUD-005、AUD-009。

1. 对死亡率和生育率的样本暴露量统一分母：
   - 使用地区总样本数乘地区总人口比例；或
   - 使用性别样本数乘“该年龄性别比例 / 该性别总比例”。
2. 在 data-audit.md 和公式中明确 S、p 的分母、单位以及由率到概率的解释。
3. 全国出生性别比改为

\[
100\frac{\sum_r B_{r,m,t}}{\sum_r B_{r,f,t}}.
\]

4. 增加 2005 城市 20 岁男性的手算回归测试。

验收标准：

- 手算暴露量与代码一致：pass。
- 两种等价暴露量写法在样本性别构成与比例一致时给出同值：pass。
- trajectory 出生性别比与逐地区男/女出生数汇总一致：pass。

## P1：重做验证，限制模型选择声明

关联 finding：AUD-003、AUD-006。

1. 使用 2003、2004、2005 滚动起点验证；每一折只用过去年份。
2. 异常年识别、率平滑、迁移拟合和所有超参数选择都在训练折内完成。
3. 分开报告：
   - 全国年龄—性别和年龄段误差；
   - 地区份额与城镇化误差；
   - 多步全国总量误差（若数据不足则 needs_review）。
4. M1 作为更简单的全国结构基线长期保留；M2 只有在地区预测证据和题意覆盖上单独说明优势。
5. 报告各折均值、标准差、最坏折和排名稳定性；不要只报告最有利年份。

验收标准：

- 每折训练数据最大年份严格小于留出年：pass。
- anomaly_years 不读取留出折：pass。
- 2003--2005 全部滚动结果可复现：pass。
- 若模型排名跨折反转，selection_judgment 必须为 needs_review；只有预先定义的稳定性门槛通过才可写 pass。

## P1：改为联合/全局稳健性

关联 finding：AUD-004。

1. 至少完成现有 54 个全因子组合；更理想的是拉丁超立方或 Sobol 全局敏感性。
2. 同时报告峰值、峰值年、2050、2100、65+ 占比和抚养比的联合范围。
3. 对 2003 生育率处理拆成两个问题：
   - 不重新归一的水平不确定性；
   - 固定标准 TFR 后的年龄/地区形状不确定性。
4. 对 prior_exposure、死亡平滑强度、死亡率到概率的转换和迁移年龄选择做附加敏感性。
5. 图题明确区分“单因素变化”与“联合条件包络”，不得把前者称为总体范围。

验收标准：

- 54 个原范围组合全部成功、非负且守恒：pass。
- 报告能复现最大峰值约 15.538 亿这一当前反例，或对修正模型给出新的可追溯最大值：pass。
- 交互效应和联合范围进入正文，而不只留在附录：pass。

## P2：拆分运行状态与科学判断

关联 finding：AUD-007。

建议输出三个互不替代的字段：

- execution_judgment：程序、依赖、退出码和文件生成是否成功；
- internal_invariants_judgment：守恒、非负、边界和数值稳定性；
- scientific_validation_judgment：定义、外部/留出验证、稳健性和数据适用性。

overall_judgment 取三者中最严重状态；存在 contextual fail 或 scientific needs_review 时不得写总体 pass。运行清单主要承担可追溯性，不应声称数学正确。

验收标准：

- clean run 可以同时表达 execution=pass、internal=pass、scientific=needs_review/fail：pass。
- consistency checker 不再把“发现并披露 fail”转写成无限定的总 pass：pass。

## P2：修复论文工件与一致性检查

关联 finding：AUD-008。

1. 从 main.tex 或同一结构化源可靠生成 paper.md，禁止把 LaTeX 反斜杠通过普通转义字符串写入。
2. 增加 C0 控制字符检查；除换行和经过说明的制表符外，任何退格、换页等均判 fail。
3. 检查所有硬编码表格与 fresh results，而不只搜索少量数字片段。
4. 检查关键公式：
   - 全国标准 TFR；
   - 出生性别比分配与全国汇总；
   - 暴露量分母；
   - 迁移守恒；
   - 90+ 开放组。
5. PDF 编译后立即写入同一次冻结清单，并做逐页视觉检查。

验收标准：

- paper.md 异常 C0 字符数为 0：pass。
- Markdown、LaTeX、PDF、代码和结果表的关键数字/公式一致：pass。
- PDF 实算哈希与全部记录一致：pass。

## 推荐执行顺序

1. 在新 solve 工作区实施数学与数据修正。
2. 完成滚动验证和联合稳健性。
3. 重新生成所有结果、图表、论文与判断字段。
4. 运行扩展一致性检查。
5. 原子化生成完整 FROZEN 清单并设为只读。
6. 新建最小 audit 工作区，从零验证清单和哈希。
7. 只有新 audit 的完整冻结、数学定义、复现和验证均达到相应门槛，才允许把综合状态改为 pass。
