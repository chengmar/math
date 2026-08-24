# 2005A 冻结解答独立审计报告

## 结论

**overall_status: fail**

审计在冻结完整性门禁处停止。阶段锁与路径清单校验均为 `pass`，但冻结解答中唯一可定位的逐文件 SHA-256 清单 `results/reproduction-manifest.json` 有两项哈希不一致。因此，依据 `$cumcm-a-audit` 的“清单或逐文件哈希不一致即停止”规则，本会话未进入干净环境复现、数学正确性审查、稳健性试验或反例构造。

本报告不把未执行事项表述为通过，也不推断哈希差异的原因。

## 完整性门禁

| 检查 | 状态 | 证据 |
|---|---|---|
| audit 阶段锁 | pass | `scripts/check_phase.py` 退出码 0，stdout 为 `[PASS] audit 阶段锁有效` |
| `allowed-paths.json` 对阶段锁哈希 | pass | 期望值与实算值均为 `d26656710e6fa20687192d47a379f19e9dfb5600d881d903946021fef6d4db7e` |
| `forbidden-paths.json` 对阶段锁哈希 | pass | 期望值与实算值均为 `f075bf741aa5e0dc4527893642bc07eb4bc70edc84733fa9b1801ed5ccaa0b47` |
| 原始题目与冻结副本一致性 | pass | 两者 SHA-256 均为 `cea35513e302801d4504f3febdf444af783d928c4748bdf91a21724a910e271a` |
| 原始附件与冻结副本一致性 | pass | 两者 SHA-256 均为 `ddd7b8e70aa727a2858e2476ddfeda7e3042be09d55b304297f990a203071e4f` |
| 清单列出的 106 个文件逐项校验 | fail | 2 项哈希不一致，见下表 |
| 冻结树清单覆盖性 | needs_review | 冻结树另有 88 个文件未列入该清单；未提供可定位的全树 FROZEN 清单 |

### 哈希不一致

| 文件 | 清单 SHA-256 | 实算 SHA-256 | 状态 |
|---|---|---|---|
| `paper/main.log` | `6f4f3b1fd2bbd6deea4ba4d42b621b70a7acaa7b1daeb3924418083403753856` | `964b79732d9047d66e6aa8556d6d03e417a91762d114c811705b7a72dd635f2e` | fail |
| `paper/<SOURCE_FILE_REDACTED>` | `cf81e844e69f3debe8e49ee9b969589d851cf729ebe028253fff8e59ad8c2763` | `8fa23bfe752750cc1190eeef9fcd948a07e05d43974ca4e0f0e7c5a26838ca9d` | fail |

## 未执行范围

以下项目状态均为 `needs_review`，原因仅是完整性门禁阻断，而不是发现其数学上错误或正确：

- 干净环境依赖安装与完整重跑；
- stdout、stderr、退出码及生成输出对比；
- 小问覆盖、公式推导、单位量纲、约束、边界/初值、可辨识性、可行性、有界性与收敛；
- 随机稳定性、泄漏、验证集混用、因果误读、过拟合与异常敏感性；
- 论文—代码—图表一致性；
- 反例、极端输入、简化案例与更简单模型比较。

## 冻结版本保护

`frozen-solution` 未被修改；未读取参考论文、Vault、讲评或标准答案；未联网检索当前题目或现成解答。
