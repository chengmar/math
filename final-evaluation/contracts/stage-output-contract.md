# 三组统一阶段输出合同

每组每轮固定使用三个新 ephemeral Thread：Solve、Audit、Blind Revision。Blind Final 是 Blind Revision 的冻结结果，不新增模型调用；Reflection 禁止。

Solve 必须在本组工作区生成完整的 `submission/blind-v1/`：代码、机器可读结果、图、论文源文件与 PDF、`solution-report.yaml`、`reproducibility.yaml`、数据审计、假设、变量和模型选择。先复现，再冻结 `FROZEN_BLIND_V1.json`。

Audit 只能读取本组冻结的 Blind V1，输出 `audit/` 下的审计报告、结构化 findings、反例、独立复现报告与 revision plan，不得修改 Blind V1。

Blind Revision 只能读取本组 Blind V1 和本组 Audit，输出 `submission/blind-final/` 与 revision response。Revision Verification、复现、论文构建和泄漏门禁通过后冻结 `FROZEN_BLIND_FINAL.json`。

每阶段必须保存：Thread ID、requested/actual model、reasoning、fallback、ephemeral、开始/结束时间、退出码、资源时间、输入/输出哈希和泄漏报告。任何模型合同不匹配使该组该轮无效，不得换模型补齐。

三组不得读取其他组目录。盲评分之前不得读取参考论文。文件数量、论文长度和代码行数不得作为质量得分。

