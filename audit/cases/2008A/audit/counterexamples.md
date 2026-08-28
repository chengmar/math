# 反例与极端检查记录

- overall_status: `needs_review`
- mathematical_counterexamples: `needs_review`

冻结完整性门失败后未进入数学模型与数值代码审查，因此没有执行图像扰动、退化几何、极端阈值、初值变化或随机重复试验。以下仅记录与冻结/来源链有关的反例，不把它们冒充数学反例。

## CE-01：未列文件可变化而局部清单仍通过

- status: `fail`
- target_claim: “现有清单足以证明整份冻结解答未变化”
- construction: 假设 `paper/paper.md` 等未进入任何 SHA-256 映射的载荷文件发生变化；`reproducibility.yaml` 的 10 个声明哈希和 `run_manifest.json` 的 17 个输出哈希都可以继续通过。
- evidence: `frozen-solution` 有 178 个普通文件，而现有两个清单仅覆盖各自局部输入、代码或输出，没有整树文件集合与逐文件哈希。
- implication: 局部运行清单不能替代冻结清单，完整性声明被反例否定。
- executed: false
- reason: 这是无需修改冻结文件即可成立的结构性反例；本 audit 会话没有实际篡改任何文件。

## CE-02：输出哈希全通过但代码来源链仍失败

- status: `fail`
- target_claim: “产物哈希通过即可证明当前代码生成了这些产物”
- observed_case: `run_manifest.json` 的 17 个输出哈希全部 `pass`，但当前 `code/solve.py` 的哈希为 `f3fc1ad7...c3017`，与清单声明的 `a6f8bdf0...40c6` 不同。
- implication: 现有目录已经是该命题的直接反例；产物完整性与代码—产物可追溯性是两个不同条件。
- executed: true

## 待重新冻结后执行

以下项目状态均为 `needs_review`：

1. 圆目标缺失、粘连、遮挡与少于四个有效点的退化案例。
2. 阈值越界、全黑/全白图像、强 JPEG 噪声与亚像素轮廓异常。
3. 共线/近共线布置、掠射角、单应矩阵病态与正深度约束反例。
4. 多初值、多随机种子、扰动幅度扫描及重复运行哈希稳定性。
5. 主点偏差、焦距误差、镜头畸变和靶标非平面所引入的系统偏差。
