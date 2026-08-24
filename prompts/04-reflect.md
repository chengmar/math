使用 $cumcm-a-reflect 比较最终冻结盲解与 approved-references 中的多篇材料。

开始前验证 FROZEN_BLIND_FINAL.json。不得覆盖或伪造盲解历史，不默认参考论文正确。比较模型、结果、验证、复杂度、解释性和表达；区分独立想到与看参考后学到。只生成 candidate；Dummy 只生成 demo，不能直接 verified。

`blind-final/FROZEN_BLIND_FINAL.json` 与 `blind-final/REFLECTION-BOUNDARY.json` 是外部调度器在 Blind Revision 会话结束后生成并验证的权威冻结证据。解题会话内的 `frozen` 自报字段属于冻结前记录，不得用它否定外部冻结；Audit 的进入条件是已完成并提供报告，不要求所有数学结论均为 pass。必须自行核对权威冻结清单的文件哈希，校验通过后再读取 approved-references。

禁止联网搜索当前题目或额外现成解答；只能使用当前工作区已批准复制的 2 至 4 篇参考材料。

优先使用无界面的文本提取或 OCR 工具读取参考材料，不要在 WPS、Word、浏览器或 PDF 阅读器中留下打开的临时参考文件；若确实启动了图形界面，必须在最终回复前关闭该参考文档并确认文件句柄已释放。
