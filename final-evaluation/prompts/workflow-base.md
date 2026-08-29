# B/C 组共同冻结工作流

严格使用当前工作区内复制的 AGENTS、阶段 Skill、论文模板和统一输出合同。只读取当前阶段明确允许的本组路径；不得搜索现成答案，不得读取历史案例、历史解答、历史 Reflection、Candidate、其他组输出或参考资料。

Solve：执行数据审计、单位检查、简单基线、最多三个主要候选模型比较、最简充分选型、变量/假设/推导、固定随机种子代码、机器可读结果、独立验证、稳健性/误差/边界分析和完整论文。

Audit：使用独立新 Thread，仅审计本组冻结 Blind V1，输出结构化 findings、反例、复现报告和 revision plan，不得修改冻结件。

Blind Revision：使用独立新 Thread，仅依据本组 Blind V1 与 Audit 修订，完成 Revision Verification、关键重跑和可冻结 Blind Final。

严格遵守工作区内的统一输出合同。不得进行 Reflection。

