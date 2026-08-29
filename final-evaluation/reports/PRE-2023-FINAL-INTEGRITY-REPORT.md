# CUMCM-A-Lab 2023A 前最终完整性核验报告

- 核验时间：2026-08-29（Asia/Shanghai）
- 核验阶段：第一阶段，只读完整性核验
- 总判定：`pass`
- 2023A 状态：`test_sealed`，`consumed=false`
- 2023A 题面正文、官方附件正文和参考论文：未读取、未复制、未启动任何 2023A 模型会话

## 1. 硬门结论

第一阶段列出的硬门均未触发：18 个训练案例的两版冻结树均通过逐文件 SHA-256 核验；最终知识快照及其绑定对象哈希匹配；Git 未发现真实语料；未发现 2023A 不透明封存文件的精确哈希进入源仓库、训练记忆或脱敏导出；源工作树无无法解释的修改；全部测试、回归和定向篡改测试通过；2023A 仍未消费。

本报告中的 `pass` 只表示文件身份、流程元数据、冻结边界和自动化门禁通过，不等同于对 18 篇历史解答数学结论的重新证明。

## 2. Git 与远端映射

- 源项目分支：`training/full-corpus-v1`
- 源项目 HEAD：`24c8ef9bc6cf2fd447b40c649f860553f6ba4297`
- 源工作树：clean；`git diff --check` 通过
- 源项目 `origin`：`https://github.com/chengmar/all-in-math.git`
- 源分支相对 `origin/main`：领先 36、落后 0；远端没有同名 `training/full-corpus-v1` 分支
- 未推送框架修改判定：没有未提交修改；源 HEAD 已以脱敏进度映射发布到 `math.git/full-training-progress`
- 脱敏仓库：`https://github.com/chengmar/math.git`
- `full-training-progress`：`24c8ef9bc6cf2fd447b40c649f860553f6ba4297`，与源 HEAD 精确相同
- `full-training-complete`：`3a4ab32b67054eddc3623b2e36950e043edd49c1`，与指定远端 HEAD 精确相同
- 本地脱敏分支和最终重克隆均位于 `full-training-complete@3a4ab32` 且工作树 clean

源提交与 `full-training-complete` 是不同层次：前者是完整训练框架历史，后者是内容白名单和脱敏规则生成的审计快照。因此两个提交 ID 不相同是正常导出映射，不是损坏。

## 3. 训练案例逐案磁盘与哈希核验

每案均验证 `FROZEN_BLIND_V1.json`、Blind V1 全文件哈希、Audit/Blind Revision/Reflection 会话、`FROZEN_BLIND_FINAL.json`、Blind Final 全文件哈希、四 Thread 唯一性、实际模型、推理档位、fallback、ephemeral、Blind Final 后才进入 Reflection 的边界证据，以及临时参考副本清理报告和目录不存在性。

| 案例 | Blind V1 文件/树哈希 | Blind Final 文件/树哈希 | 会话合同 | 参考开启边界 | 临时参考清理 | 结论 |
|---|---|---|---|---|---|---|
| 2004A | 183 / `9fb12ffa51dd253f7d5a4e4a018baa8e36cdccfac33cde704f3501338c92e57b` | 251 / `84cb8169774cbb05bf5570e8fa8d5ab3cc4036c3997d4a60dce8e492d54e9889` | pass | pass | pass | pass |
| 2005A | 188 / `05deba95d7309679a91ce5f790a4bbd4267be664cf5da244dbb4ab4be36943ee` | 305 / `0dc84a05f450081b4ae42a0796bd97a729b175bddeb068ea44a1e5b4946a92d1` | pass | pass | pass | pass |
| 2006A | 139 / `28a5f1357f53260200a2e3c1f5805e2ea7bb2a90c9b36427b9295b620fd842e0` | 234 / `fdb0474839e6d9195c87acee59d49e2e7a3359b92a19c3b16f048f1ebc97f898` | pass | pass | pass | pass |
| 2007A | 174 / `be7bd307f334b4d60148c51c7d714d738018ccc1e7dbefcf0ae99206304921bd` | 427 / `34a9c4c0ca557403881009b8438c6472e7c966d9a6845a7e32b9172e56f610f0` | pass | pass | pass | pass |
| 2008A | 176 / `73142d335e8c9e9ec5d3b0724487b24967e50a414ed306fbd29f88a2273a780d` | 268 / `89655a778d2ac555447a2eb2d7ae403de641c9d51678d76a606d8b56102f00dc` | pass | pass | pass | pass |
| 2009A | 111 / `f774f688a8574b3f59ae6e613ca9da1a920b69222734642e7217eebb4799c3fd` | 170 / `2817fed22307980f4e4bc367abd974d83f9dee2be5c2972f51b1e3619d0703a0` | pass | pass | pass | pass |
| 2010A | 135 / `91c489ae2ee337c864648d8772b3a4f6c91818b61f685aeecdcd043e343a558d` | 205 / `5fee1bdf0628de70c3d20b144ff8370de071999cf58baf7e28ad80f00f997412` | pass | pass | pass | pass |
| 2011A | 112 / `c5b0e04d2c0a233bae1dd2209bfc2a3ebd09dd8114d48b613eca15fd921e572f` | 222 / `14582dbe3b35185bc6347bee7c515920027ed486e4cb4d69b931ba7f0a9d2e7d` | pass | pass | pass | pass |
| 2012A | 99 / `c7b8662553627237519783b0a241a365f7603843278b161a1d7a9429e5cd0192` | 213 / `d906f6cb698512b164213c40b53ba8669af1b01264a2994108904b353f8c51a9` | pass | pass | pass | pass |
| 2013A | 62 / `fed823dd6b3f10cfb554040c4e6955c179861964db5e2f5af3ca4fa8ba26b94e` | 124 / `33c63844e6c689024834ae957e9fce92ac6a8cf78832dbc875ea0d274a6ab2d4` | pass | pass | pass | pass |
| 2014A | 90 / `66d4bf5889a51f2150825f671b7340699468df5d699f4064d20cc94901a9f556` | 172 / `185995a94c89959e97fdc039c8a18466d9af102998b6761e44e908d14059fb03` | pass | pass | pass | pass |
| 2015A | 78 / `e2280802035f6194a85fb6f2c7d42a8f16ebab9313748a94599edcc9caa90a2f` | 160 / `0ed72b3e904a8c13c5b633864a383e234375d56f0072d701264195f626311874` | pass | pass | pass | pass |
| 2016A | 65 / `e1e04fb77a18a10f29b6cc2ea3a7bb3f477b7cbbec1d41dab8a629ce3a6528ab` | 214 / `c41e7a0b7dcab606e87e799efa454f2fc93e66c815df6e5b10382ad0952cef1f` | pass | pass | pass | pass |
| 2017A | 164 / `6937f8f0c56e25a454c86e7cd3971e3c5432c1dd37db317cab48f9273d229053` | 436 / `5c1e8e07a9ad40bba8c74b377796a44e300a1b8fc5953db547d693f9179351c5` | pass | pass | pass | pass |
| 2018A | 72 / `3a1e51cee374165411522586c1d5b0d16d6f442e1ca7b14fa6459d38fb481109` | 148 / `39e7e2583662e357ea27cf889060fd88e504b3ddfa884bdb67eafccb87884792` | pass | pass | pass | pass |
| 2019A | 74 / `9adbecdb7a06f08ad8f492c9f26e520bd7ea40e5bab82b316ec5cb475b7d7b7e` | 141 / `e564523890d91ae117f6bac397bd860b5ca52355ce6df42d01ad0a3cf06b0196` | pass | pass | pass | pass |
| 2020A | 71 / `14e3694cebb6965a4d07146342acf8fa64046a608e0fea9103d01cd443329f5b` | 140 / `71c54e4bf0743b10c68590356f52284c13a953829b1d556cd99c3c3d549a57b8` | pass | pass | pass | pass |
| 2021A | 64 / `cd959e705337e1eb757c9fc7192db8289503abda1cc4cdf5689ff8f026900032` | 115 / `512b8d2173b12e0656284d965d1b9b4697a0cfec1e07ae84c351e1ae69e85c3e` | pass | pass | pass | pass |

所有 72 个阶段会话均满足：`model` 字段作为实际模型记录，值为 `gpt-5.6-sol`；`reasoning_effort=max`；`fallback=false`；`ephemeral=true`；同一案例的四个 Thread ID 互不相同。

队列实况：2003A=`deferred_platform_safety`；2004A—2021A 共 18 个=`completed`；`completed_with_caveats=0`；`incomplete=0`。

## 4. 最终知识快照

- 快照：`reports/knowledge-snapshot-before-2023.json`
- 当前实际 SHA-256：`6c261d4a226863a2524017e9ee50c1be25985de1b74637be89581daffccd1561`
- 状态：`training_complete_ready_for_final_test`
- 快照源提交：`df05f8c9865ba092d58c52425321addb6d455482`，生成前工作树 clean
- `AGENTS.md`：`2f9b19cf0d4cb8e2964aeb58cddcb9dca7abcfd999c9a63ac9e945faac4b7a10`
- `cumcm-a-solve`：`45aab1c11080ed5ec907bb8499e9e48436bf178474702b70c58bf6620eba8df1`
- `cumcm-a-audit`：`6832414ee9c863e557147d37727dc427687fb9274a0b817d0e88928a1a8d1591`
- `cumcm-a-reflect`：`b797e56b747e9a5a39fdbb91eb125ed710919c88f49fde7faa0ef6b0facab091`
- `cumcm-a-evaluate`：`ae47af98ac4590f3dff333eaec8952948b651291ab6378459c7061dd82355915`
- training-memory 树：`4efa9dea17d350dadd8fd1d9a7608301b106cf1a2a849ffd5bc25a2d8ad72226`
- 快照字段 `model_policy_sha256` 的代码绑定对象为 `runtime/execution-policy.json`：`19208ed204c175cf044858c5f6fbdedf75d40f9e13359efcf51053ef378fc2cd`
- 额外核验 `config/training-memory-policy.yaml`：`0107fb096906c96c004b07f3a5218ebd116caaf6b937b1a403d0e44d3f5ea82b`

22 张索引卡全部存在，ID 与文件名一致，状态均为 `provisional_training`，并具有代码定义的 18 个必需字段。`provisional_at_risk=0`、`machine_verified=0`、`verified=0`。

Candidate 有效总数为 323，与快照一致；2016A—2021A 为 123。2014A 的旧候选包不符合当前结构，按既有规则计入 `legacy_invalid_candidate_packages` 并从有效 Candidate 数排除，不影响 2014A 案例完成状态。

2023A 未进入训练记忆的证据范围：22 张卡及索引中无显式 `2023A` 标识；训练记忆文件哈希与 2023A seal 中 8 个不透明文件哈希无匹配。全程未打开 2023A 内容，因此不做正文片段级比对。

## 5. 训练记忆实际使用事实

- 2004A—2021A 正式 Solve 实际检索卡片总数：0
- retrieval-log 中实际卡片 ID：无
- `adopt=0`、`adapt=0`、`reject=0`、`retrieved_only=0`
- 22 张卡的 `actual_usage_years`：全部为空
- 正面跨题证据记录：0
- 负面跨题证据记录：0

22 张卡是“已创建的 provisional_training 卡”，不是“历史中已实际使用 22 张卡”。默认 evaluation 规则也不会使用 provisional_training；2023A 三组实验若继续，只能按预注册的 C 组受控例外读取，且不得升级状态。

## 6. 测试、回归与扫描

- 全部 pytest：`199 passed in 28.35s`
- 冻结/阶段/封存/Shadow 定向篡改测试：`4 passed in 1.27s`
- 全量回归：`pass`；dummy-a 三维均无退步，报告为 `PRE-2023-REGRESSION-REPORT.yaml`
- Git 真实语料泄漏守卫：`pass`；检查 509 个 Git 候选文件，0 findings
- source secret 扫描：`pass`；509 个文本文件，0 findings
- 2023A 精确哈希泄漏扫描：`pass`；源仓库、training-memory 和最终脱敏导出均为 0 matches
- 脱敏导出规则扫描：`pass`；检查 1084 个文件，0 findings；真实题面、参考论文、原始数据、认证文件、2023 内容导出计数均为 0
- `git diff --check`：pass
- 当前实际训练进程：0；PID、lock、nonce 均不存在

Secret/泄漏扫描无法证明模型预训练阶段绝对未见过相似内容；此处结论仅覆盖本地文件、Git 树、已知真实语料哈希、2023A 不透明 seal 哈希和允许读取的训练题面长片段规则。

## 7. 导出文件计数口径

不存在真实文件缺失，差异来自两个自引用文件和最终报告追加提交：

| 提交/口径 | Git 树文件 | `PUBLISH_MANIFEST` 文件数 | 哈希清单条目 | 扫描文件 | 说明 |
|---|---:|---:|---:|---:|---|
| 内容提交 `dadbb32` | 1083 | 1083 | 1081 | 1083 | 哈希清单排除自身与 `PUBLISH_MANIFEST.json`，所以 1083−2=1081 |
| 最终 HEAD `3a4ab32` | 1084 | 1084 | 1082 | 1084 | 追加 `FINAL_TRAINING_REPORT.md` 后增加 1；仍排除两个自引用文件，所以 1084−2=1082 |

因此历史报告中的 1081、1083、1084 分别是内容提交的哈希覆盖数、内容提交/当时扫描文件数、追加最终报告后的最终树/当前扫描文件数。当前 1082 是最终 HEAD 的哈希覆盖数。全部 1082 个条目重新计算均匹配，排除项严格为 `EXPORT_FILES.sha256` 和 `PUBLISH_MANIFEST.json`。

## 8. 第一阶段停止点

完整性硬门结论为 `pass`，允许进入“独立评测分支、预注册、隔离探针和三组空工作区准备”。在预注册文件冻结且隔离同时满足允许读取、禁止读取和工作区代码执行之前，仍不得读取或复制 2023A 题面、附件及参考资料，也不得执行不可逆 `consumed=true` 转换。
