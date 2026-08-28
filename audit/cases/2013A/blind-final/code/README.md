# 代码说明（盲修订）

本目录只使用白名单输入，不下载数据，不访问题面链接，也不修改 `blind-v1/`。

- `model-core.ps1`：全精度三角基本图、反射事件积分和视频标注容量管线。
- `independent-validator.ps1`：不读取解析速度，从原始参数按净积压 PCU/密度差重建长度。
- `charts.ps1`：生成五张有限边界和敏感性图。
- `solve.ps1`：核验输入路径/大小/SHA-256/OLE 文件头，运行 Q1 管线与单元测试，生成四问结果、信号反例、有限边界表、独立验证和图。
- `verify-paper.ps1`：核对隐藏标记以及注释外可见数字、公式、边界、图引用和 TeX 静态结构；PDF 单独保持 `needs_review`。
- `reproduce.ps1`：连续运行两次 `solve.ps1`，比较声明生成文件的大小和 SHA-256。
- `run-all.ps1`：依次运行求解、论文一致性和双跑复现。

在工作区根目录运行：

```powershell
pwsh -NoProfile -File .\code\run-all.ps1 -Workspace .
```

若以后在同一授权阶段补齐视频与标注，可运行：

```powershell
pwsh -NoProfile -File .\code\solve.ps1 -Workspace . -AnnotationPath .\input\annotations\<SOURCE_FILE_REDACTED>
```

标注列见 `results/<SOURCE_FILE_REDACTED>`。只有完整约 60 s、质量标志清楚且需求饱和的窗口才进入容量估计；未饱和通过率只是下界。视频文件即使出现，也不会仅凭扩展名或文件数得到 `pass`，还需要不可变身份和解码元数据。

随机种子固定为 `2013`，当前算法无随机步骤。内部 `pass` 只属于声明实现、守恒性质和确定性复现；视频缺失、外部有效性、完整来源访问史人工复核及 PDF 目视均保持 `needs_review`。
