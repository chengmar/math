# CUMCM 高教社杯 A 题能力训练实验室

这是一个本地、可版本控制的训练框架，用阶段隔离、可复现代码、冻结哈希、独立审计、参考复盘、知识升级门槛、回归和盲测来提高数学建模能力。它不修改 Codex 权重，不是参数微调，也不会把“读过获奖论文”当成已经学会。

本版本不含真实赛题、真实答案或获奖论文；`dummy-a` 完全由人工小数据构成，只验证流程。

## 四类持久信息的区别

- `AGENTS.md`：每次任务都必须遵守的稳定纪律，短而固定。
- `.agents/skills`：四个显式阶段工作流；每个 Skill 的隐式调用均已关闭。
- `knowledge`：Git 管理、带来源和适用边界的知识卡。solve/evaluation 只读 `machine_verified` 与 `verified`。
- Memories：模型外的自动个性化记忆。本专用 Profile 已关闭读取与生成，核心经验不得依赖 Memories。

复杂算法、神经网络、遗传算法不自动等于创新；必须证明误差、稳健性、参数、计算成本、约束满足、解释性或结论价值中的实际收益。

## 安全边界

实际路径：

- 实验室：`<LAB_ROOT>`
- Git 项目：`<LAB_ROOT>`
- 参考 Vault：`<VAULT_ROOT>\reference-vault`
- 考试 Vault：`<VAULT_ROOT>\exam-vault`
- 专用配置：`<LAB_ROOT>\codex-home`
- Baseline 配置：`<LAB_ROOT>\baseline-codex-home`

两个 Vault 在 Git 仓库外，没有符号链接进入 trainer。系统只在专用 `CODEX_HOME` 中禁用非项目自定义/插件 Skills；没有删除原 Skills、修改原 `.codex` 或卸载插件。来源不明或系统内置内容只清点、不操作。

文件隔离能发现明显泄漏，但不能证明模型内部绝对未见过类似内容。自动检查也不能完整判断数学正确性或替代人工评委。

## 批量语料与自动训练队列

`framework-v1` 之后的批量层固定采用 2003A—2021A 训练、2022 排除、2023A 最终测试封存。原始材料只从仓库外的 Intake 读取，确定性副本只写到仓库外的 Vault，真实运行案例只写到 `<RUNTIME_ROOT>`。仓库中的 `corpus/training-queue.yaml` 仅保存不透明案例状态；本机尝试次数、锁、PID 和断点保存在被 Git 忽略的 `runtime`。

批量层包括：只读 inventory、默认 dry-run 的事务导入、源/目标 SHA-256 复核、安全解压、同年 A 题匹配、独立 Curator、19 题升序队列、每阶段全新 Codex 会话、可恢复 Autopilot、Shadow Evaluation、机器验证知识门和 2023 单向封存。2023A 在显式不可逆确认前永远不会进入自动队列。

PowerShell 入口位于 `scripts`，可从任意当前目录运行并自动定位项目虚拟环境、`local-paths.toml` 与专用 `CODEX_HOME`：`initialize-corpus.ps1`、`inventory-corpus.ps1`、`import-corpus.ps1`、`validate-corpus.ps1`、`queue-status.ps1`、`run-training-queue.ps1`、`resume-training-queue.ps1`、`start-autopilot.ps1`、`stop-autopilot.ps1` 和 `autopilot-status.ps1`。导入默认只做 dry-run；已有不同哈希目标一律拒绝覆盖。

当前批量状态和平台限制见 `reports/corpus-summary.json`、`reports/training-summary.json`、`reports/autopilot-progress.md` 与 `reports/security-sentinels.json`。

## 第一次启动

在 Windows PowerShell 中运行：

```powershell
Set-Location '<LAB_ROOT>'
.\scripts\setup.ps1
& '<LAB_ROOT>\Verify-Codex-Isolation.ps1'
& '<LAB_ROOT>\Start-CUMCM-Codex.ps1'
```

`setup.ps1` 只安装 `requirements-core.txt` 中的 PyYAML、pytest、jsonschema。真实案例需要常用数值包时再执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-modeling.txt
```

启动后人工执行 `/skills` 和 `/memories` 核对。四个阶段 Skill 只能通过 `$cumcm-a-solve`、`$cumcm-a-audit`、`$cumcm-a-reflect`、`$cumcm-a-evaluate` 显式调用。阶段切换必须关闭当前会话并开启新会话。

## 第一题真实训练：从题面到知识提案

### 1. 新建案例并放题目

```powershell
Set-Location '<LAB_ROOT>'
.\scripts\new-case.ps1 -CaseId 'train-a-001' -Split 'train' -Title '训练题标题' -ProblemFamily 'mechanistic'
Copy-Item -LiteralPath '<ABSOLUTE_PATH>' -Destination '.\cases\train\train-a-001\input\problem\problem.pdf'
Copy-Item -LiteralPath '<ABSOLUTE_PATH>' -Destination '.\cases\train\train-a-001\input\data\data.xlsx'
```

先确认题面和原始附件放在 `input`；不要把参考论文、讲评或答案放入案例目录。

### 2. 盲解 V1

```powershell
.\scripts\start-phase.ps1 -CaseId 'train-a-001' -Phase 'solve'
```

脚本准备隔离工作区、显示允许/禁止资源、设置专用 `CODEX_HOME` 并启动新 Codex。复制 `prompts\01-solve.md`，显式调用 `$cumcm-a-solve`。完成后关闭会话并冻结：

```powershell
.\scripts\freeze-case.ps1 -CaseId 'train-a-001' -Version 'blind-v1' -RandomSeed 20260816 -RunCommand 'python code/run.py'
.\scripts\verify-case.ps1 -CaseId 'train-a-001' -Version 'blind-v1'
```

冻结同名版本默认拒绝覆盖。

### 3. 独立审计

```powershell
.\scripts\start-phase.ps1 -CaseId 'train-a-001' -Phase 'audit'
```

在全新会话复制 `prompts\02-audit.md`，显式调用 `$cumcm-a-audit`。审计只能写建议，不能改冻结解。五个审计输出齐全后：

```powershell
.\.venv\Scripts\python.exe .\tools\workflow.py complete --case-id 'train-a-001' --phase audit
```

### 4. 审计后盲修订并冻结 Final

```powershell
.\scripts\start-phase.ps1 -CaseId 'train-a-001' -Phase 'blind-revision'
```

在新会话复制 `prompts\03-blind-revision.md`，仍只调用 `$cumcm-a-solve`；只允许题目、数据、V1 和审计。然后：

```powershell
.\scripts\freeze-case.ps1 -CaseId 'train-a-001' -Version 'blind-final' -RandomSeed 20260816 -RunCommand 'python code/run.py'
.\scripts\verify-case.ps1 -CaseId 'train-a-001' -Version 'blind-final'
```

若审计确认无需修订，可在 `audited` 状态直接冻结 final，原 V1 仍保留。

### 5. 受控导入参考材料并复盘

只有 Final 哈希通过后才能操作。把用户选定的 2–4 篇材料放到 Vault，例如：

```powershell
New-Item -ItemType Directory -Force '<VAULT_ROOT>\reference-vault\train-a-001'
Copy-Item -LiteralPath '<ABSOLUTE_PATH>' -Destination '<VAULT_ROOT>\reference-vault\train-a-001\paper-1.pdf'
Copy-Item -LiteralPath '<ABSOLUTE_PATH>' -Destination '<VAULT_ROOT>\reference-vault\train-a-001\paper-2.pdf'
```

在 `cases\train\train-a-001\case.yaml` 的 `reference_ids` 中填写相对 Vault 路径：

```yaml
reference_ids:
  - train-a-001/paper-1.pdf
  - train-a-001/paper-2.pdf
```

然后：

```powershell
.\scripts\start-phase.ps1 -CaseId 'train-a-001' -Phase 'reflection'
.\.venv\Scripts\python.exe .\tools\workflow.py complete --case-id 'train-a-001' --phase reflection
```

新会话复制 `prompts\04-reflect.md`，只调用 `$cumcm-a-reflect`。参考论文不是绝对真值；尽量复跑代码或独立重算关键量，不复制原文语言，不改冻结历史。

### 6. 候选知识与升级

把复盘中的可迁移提案人工整理到 `knowledge\candidates`，初始必须是 `status: candidate`。默认命令只生成升级提案：

```powershell
.\.venv\Scripts\python.exe .\tools\promote_lesson.py --candidate '.\knowledge\candidates\candidate-id.yaml'
```

只有两个非同题参数变体的独立正面案例、适用/不适用条件、反例、证据、回归 pass 全部满足，且人工批准后才能升级：

```powershell
.\.venv\Scripts\python.exe .\tools\promote_lesson.py `
  --candidate '.\knowledge\candidates\candidate-id.yaml' `
  --human-approved `
  --approved-by '审核人'
```

Dummy 经验是 `demo`，升级工具会拒绝把它变为真实 verified。

## 开发集与最终考试

开发或考试案例：

```powershell
.\scripts\new-case.ps1 -CaseId 'dev-a-001' -Split 'dev'
.\scripts\start-phase.ps1 -CaseId 'dev-a-001' -Phase 'evaluation'
```

复制 `prompts\05-evaluate.md` 并显式调用 `$cumcm-a-evaluate`。提交冻结前禁止导入答案；完成后必须生成 `evaluation-submission.json`。评测经验不能直接写入 knowledge，评测也不得修改 AGENTS、Skills、knowledge 或评分规则。

考试题只建 `exam` stub，答案留在 `exam-vault`。Treatment 和 Baseline 应保持 Codex 版本、模型、推理设置、时间预算、软件、允许工具和评分规则相同：

```powershell
& '<LAB_ROOT>\Start-CUMCM-Baseline.ps1' -Workspace '<ABSOLUTE_PATH>'
```

不要根据一次 A/B 运行下确定结论；使用 `tools\compare_runs.py` 保存多次运行和波动。

## 评分、论文和回归

评分总计 100：结果准确性与可靠性 50、方法质量与简洁创新 25、论文表达与排版 25。自动项只检查客观证据；主观项从案例 `reports\judge-scores.yaml` 读取。缺少人工证据时得分为 0 且状态为 `needs_review`，不会自动给高分。

```powershell
.\scripts\score-case.ps1 -CaseId 'train-a-001'
.\scripts\run-regression.ps1
.\scripts\run-tests.ps1
```

论文模板在 `templates\paper`。检查规则只从 `config\competition-rules.yaml` 读取；当前文件标记 `needs_review`，必须按当年官方规则填写，不要凭记忆写页数。

```powershell
.\.venv\Scripts\python.exe .\tools\lint_paper.py `
  --paper '.\cases\train\train-a-001\frozen\blind-final\paper\paper.md' `
  --artifact-root '.\cases\train\train-a-001\frozen\blind-final'
```

正式比赛使用 `templates\ai-use-log.csv` 和 `templates\ai-use-statement.md` 记录提示、采纳内容、人工核查和修改。

## 文件放错位置与恢复

- 参考论文误放 solve/audit/evaluation：不要继续运行；把文件移回 `reference-vault`，重新准备新的干净案例/工作区并运行泄漏检查。不要在已有冻结后补改历史。
- candidate 误放 solve：停止运行，把受污染的未冻结工作区整体移到 `archives` 下的隔离目录并另建案例。若已冻结，保留证据并判该次运行无效；不要覆盖清单。
- 答案误放 trainer：立即停止评测，把答案移到 `exam-vault`，保留泄漏报告并重新进行独立盲测。
- 非空工作区：准备脚本会拒绝覆盖。先人工确认内容属于哪次运行，再使用新案例 ID；不要强行清空历史。
- 冻结校验失败：不要修改清单“适配”文件；保留现场，依据审计决定该版本无效并新建冻结版本/案例。

用户级 `<USER_HOME>\.agents\skills` 已做只读备份（源目录当时为空）。恢复脚本位于 `<LAB_ROOT>\archives\user-skills-20260816-151835\restore-skills.ps1`；目标非空时默认拒绝覆盖，只有人工核对后才传 `-Force`。

## 更新规则与备份实验室

更新比赛规则：先从当年官方文件人工填写 `config\competition-rules.yaml`，保留来源和年份，再运行论文检查与测试。

备份时关闭 Codex 和正在运行的脚本，复制两个根目录到新的明确路径；不要把 Vault 纳入 trainer Git：

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item -LiteralPath '<LAB_ROOT>' -Destination "<ABSOLUTE_PATH>" -Recurse
Copy-Item -LiteralPath '<VAULT_ROOT>' -Destination "<ABSOLUTE_PATH>" -Recurse
```

Git 只管理 `trainer`。重要修改后运行测试和回归，再有意提交；不要修改全局 Git 用户配置。

## 常用统一 CLI

```powershell
.\.venv\Scripts\python.exe .\tools\workflow.py init
.\.venv\Scripts\python.exe .\tools\workflow.py prepare --case-id dummy-a --phase solve
.\.venv\Scripts\python.exe .\tools\workflow.py freeze --case-id dummy-a --version blind-v1
.\.venv\Scripts\python.exe .\tools\workflow.py prepare --case-id dummy-a --phase audit
.\.venv\Scripts\python.exe .\tools\workflow.py status --case-id dummy-a
```

`pass` 表示已满足该自动检查的有限边界；`fail` 表示有确定违规或不一致；`needs_review` 表示脚本不能可靠代替人工判断。
