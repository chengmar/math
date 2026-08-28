# 证据化修订计划

本计划只说明如何修订，不修改冻结版本。所有完成判断只使用 `pass`、`fail` 或 `needs_review`。

## P0：修正会改变方法有效性的 major 项

### 1. 重写问 4 观测方程

- 明确 `G_pg`（pixel-to-ground）或 `G_gp`（ground-to-pixel），不能混用。
- 每次单应变换后做齐次归一化 `pi(.)`。
- 若 `G` 来自控制点，主反演中固定它并传播标定协方差；若联合优化，加入控制点重投影残差、尺度规范和可辨识性约束。
- 缺视频时：`video_input_status=fail`、`numerical_location_status=needs_review`；公式未通过合成数据前：`method_status=fail`。

验收：构造已知单应、已知地点日期的合成视频点，正确参数残差接近 0，单位逐项闭合，反向矩阵约定测试均为 `pass`。

### 2. 用能覆盖实际残差的误差模型重做敏感性

- 保留现有舍入实验，但重命名为 `rounding_only_conditional_sensitivity`。
- 检查残差图、自相关和异方差；为系统残差加入模型差异项。
- 增加时间块残差自助法、毫米级单点/多点污染、时钟偏差、太阳公式选择和杆倾斜/地面不平敏感性。
- 使用稳健损失或明确说明普通最小二乘的 breakdown 风险。

验收：扰动尺度不得小于未解释残差；所有区间注明来源，不能把模型内扰动称为统计置信区间。若仍无法给总体误差，状态为 `needs_review`。

### 3. 报告日期—地点联合不确定性并降低精度

- 对每个季节/手性分支输出 `(date, latitude, longitude, height)` 联合样本或候选簇。
- 分开给“日期固定条件下”和“日期可跳变时”的地点范围。
- 增加前缀/滚动切分参数漂移、大圆距离和污染曲线。
- 经度精度必须同时受时钟精度约束；坐标小数位按可支持的公里尺度舍入。

验收：附件 2/3 相邻日对应的 16—39 km 跳变和附件 1 的 14 km 切分漂移在结论附近可见；未获得外部真值时地点真实性维持 `needs_review`。

## P1：修复验证和复现证据链

### 4. 重新设计模型选择与最终测试

- 在读取最终测试指标前，用训练段和任务结构预先冻结候选、阈值和选型规则。
- 使用滚动起点或嵌套时间切分；保留一次真正未参与选择的最终测试。
- 加入二次/三次轨迹基线，区分“短弧预测能力”和“能输出地理参数”。
- 不再按模型名称硬编码 `selected_for_main_results`；由预声明规则生成。

验收：文档中不存在“末 7 点既选择又只评价”的矛盾；选择日志可由训练数据独立重建，最终测试只运行一次。否则为 `fail`。

### 5. 从空目录重建并重新冻结

建议顺序：

1. 校验不可变输入清单和阶段锁。
2. 清空新的构建目录中的 `results/figures/generated-values`，不要在原冻结目录运行。
3. 运行 `run_all.ps1 -SensitivityReplicates 100`。
4. 运行 `validate_outputs.ps1`、覆盖全部分支的 `crosscheck_search.ps1`、`check_paper_consistency.ps1`。
5. 运行 `verify_all.ps1 -Rerun -SensitivityReplicates 100`。
6. 把每份检查表所检查源文件的 SHA-256 写入表内。
7. 最后生成 FROZEN 清单，不再编辑论文。

验收：除明确列出的动态元数据外，干净复现与冻结产物逐项 SHA-256 `pass`；`deliverable_checks` 中 main.tex 字节数等于最终文件，paper check 包含最终 bibliography。

## P2：加固输入、边界和论文一致性

### 6. 输入和物理可行域

- 在强制转换前检查 Excel 单元格 null、空串、类型和有限性。
- 验证工作表集合、时间严格递增且有正方差。
- 把 `s` 定义为离散集合 `{−1,+1}`；要求 `H>0`，识别全零/近零影子退化。
- `SolarAt` 返回真实高度与独立的 `shadow_valid`，不要用 −90°替代所有夜间高度。

验收：空单元格、重复时间、H=0、极夜四个反例均被明确拒绝或正确标记；当前数据仍为 `pass`。

### 7. 论文和结果语义

- 状态计数、候选表和关键数字直接由结果文件生成。
- Markdown/TeX 检查加入 TAB、裸 CR、NUL 等控制字符扫描。
- q3 使用 `branch_rank` 和 `global_rmse_rank`，避免 rank 含混。
- “32 项均 pass”改为实际的 31 `pass` + 1 `needs_review`。

验收：语义断言、控制字符、状态计数和最终 TeX 结构全部 `pass`；LaTeX 无引擎时仍为 `needs_review`，不得伪称编译。

### 8. 统一依赖、训练卡与引用溯源

- FROZEN manifest 填入 PowerShell/Excel/System.Drawing、seed、真实 run command 和训练卡 ID。
- 合并或明确区分根 retrieval log 与 provisional training retrieval report。
- 每条书目记录是否实际读取、来源是否在允许边界内；未读取的背景书目不能充当已验证证据。

验收：manifest、reproducibility、usage report 和论文引用之间无矛盾为 `pass`；无法追溯的引用为 `needs_review`。

## 建议重新审计的最小门槛

- 以上 P0 三项均完成并有合成/压力测试 `pass`。
- P1 的干净复现逐项哈希 `pass`。
- P2 至少修复空值检查、离散手性、状态计数和最终论文检查绑定。
- 外部地点真实性在没有独立真值前继续保持 `needs_review`。
