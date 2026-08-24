# LaTeX 文档图 lint 与恢复执行层修复报告

- 修复时间：2026-08-23
- 根因：旧 lint 只扫描主文件，未递归展开 `\\input` / `\\include`，导致分文件论文被误报缺章节。
- 文档图：支持带/不带 `.tex`、当前文件目录和论文根目录相对解析、嵌套引用、读取顺序、重复去重、注释忽略、循环检测、缺失引用报错、artifact root 越界拒绝及文件/行号证据。
- 章节识别：使用 `competition-rules.yaml` 中的可配置别名识别合并章节，不包含年份专用硬编码。
- 复现恢复：验证器会清理显式声明且限制在临时工作区内的 `-Target` / `--target`，避免旧隔离证据令新复现误失败。
- 编译恢复：XeLaTeX 在临时副本中验证，源 PDF 不再被验证器覆盖。
- CSV 收尾：排行榜写入器固定 LF，避免评分后重新引入 CRLF。
- 定向测试：LaTeX 文档图 13 项；相关 lint/verify/autopilot/scoring 测试全部通过。
- 全套测试：140 passed。
- `git diff --check`：通过（正式模型阶段启动前复核）。
- Git 真实语料守卫：通过，474 个文件、0 命中。
- 2007A 旧 Blind Revision：`reproduction=pass`、`paper_lint=needs_review`（无 fail）、`tex_compile=pass`，已冻结 Blind Final。
- 2008A 旧 Solve：`reproduction=pass`、`paper_lint=needs_review`（无 fail）、`tex_compile=pass`，已冻结 Blind V1。
- 模型重跑：2007A Blind Revision 与 2008A Solve 均未重跑模型；旧尝试数原样保留。

这是最小复制工作区隔离，不是Windows绝对路径安全证明。
