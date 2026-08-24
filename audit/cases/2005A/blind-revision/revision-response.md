# 独立审计响应

## 总体状态

`status: needs_review`

盲修订已针对审计的两项发现修改暂存交付流程，但没有、也不会在本会话自行生成或宣称 `blind-final`。当前工作区内部复核可标为 `pass` 的项目与必须留给外部冻结脚本的 `needs_review` 项目严格分开。

## 证据边界

- 只读取原题、原始附件、冻结 V1 和独立审计；未读取参考论文、讲评、答案或 Vault，未联网检索当前题目或现成解答。
- 未调用其他阶段 Skill，也未跨阶段 resume。
- 冻结 V1 未被覆盖。修订前后对其 194 个文件按“相对路径、大小、逐文件 SHA-256”排序形成的本地规范树摘要均为 `9fd8f81b95c2cd476bebe49af854e3ad6375d2c20ca117fb975260b1e4965800`，状态为 `pass`。该摘要仅证明本会话前后字节未变，不冒充外部权威冻结证明。

## AUD-001：冻结完整性

V1 状态：`fail`。

审计清单记录的 `paper/main.log`、`paper/<SOURCE_FILE_REDACTED>` 哈希分别为 `6f4f...3856`、`cf81...2763`，V1 实算分别为 `964b...35f2`、`8fa2...ca9d`。本会话再次得到相同实算值。V1 清单写入时间为 2026-08-17，而两个文件最后写入时间为 2026-08-21；据此只能确定它们在清单之后被重建，不能推断操作者或原因。

修订动作：

1. 不修改 V1，也不通过改写 V1 清单迁就现有 PDF。
2. 主入口固定为“数据提取 → 建模 → 主链精确二次重跑 → 数值验证 → TeX 编译 → 当前 PDF/日志取哈希 → 最终核验 → 暂存清单”。
3. 暂存清单写入后只运行 `code/verify_manifest.py` 只读检查，不再编译或改写被跟踪文件。
4. 当前 PDF 的哈希由 `results/paper_build.json` 从同一次最终编译即时取得；跨次 PDF 二进制稳定性仍为 `needs_review`。

修订区实际运行结果：40 个主链产物二次重跑逐项一致，状态为 `pass`；最终 PDF 为 13 页，当前 PDF 与日志 SHA-256 分别为 `9f6416fe22bba0cb14d5543a09fd2d6de1cb9f0b540e0ca4be477aeced886178`、`28f7b8f658af66a9abb535ae3c2e4d78fc040dc10112b1dc33b6ed377faf2ad2`，构建与逐页视觉抽查均为 `pass`。证据见 `results/reproducibility_check.json`、`results/final-verification.json` 和 `results/paper_build.json`；权威冻结身份仍为 `needs_review`。

## AUD-002：清单覆盖

V1 状态：`needs_review`。

修订动作：`code/verify_results.py` 枚举修订工作区的全部文件，只排除：

- `blind-v1/`：保留的冻结基线，不属于待冻结交付树；
- `audit/`：独立审计输入，不属于待冻结交付树；
- `working/pdf-preview*/`：论文视觉抽查的临时位图，不属于待冻结交付树；
- `results/reproduction-manifest.json`：清单不能递归绑定自身。

排除根、理由和文件数写入清单；`code/verify_manifest.py` 重新枚举同一范围，要求路径集合、大小和 SHA-256 全部一致。实际只读复核为列出 84 个、实有 84 个、路径差集 0、哈希/大小差异 0，状态为 `pass`。清单自身与最终冻结树必须由外部冻结摘要绑定，状态保持 `needs_review`。

## 主链复现补强

独立审计因门禁停止，没有审查内容正确性。本次按 solve 阶段完成标准继续核对时发现，V1 的 `check_reproducibility.py` 重跑遗留 `solve.py`，而论文主入口实际调用 `solve_case.py`；旧 `validate.py` 也检查另一套遗留输出。因此旧的“确定性重跑”证据不能覆盖论文主链。

修订已移除修订区内的遗留 `solve.py`，让重复性检查重新执行 `extract_docx.py -> build_data.py -> solve_case.py` 并比较全部结构化输入、核心 CSV/JSON、6 张图和论文生成表。滚动验证明细新增训练起止年与样本数，自动检查训练截止年严格早于验证年。数学外部正确性、远期预测有效性和处理量因果映射仍为 `needs_review`。
