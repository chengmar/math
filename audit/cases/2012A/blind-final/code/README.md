# Self-contained reproduction

From the blind-revision candidate root, run:

```powershell
pwsh -NoProfile -NonInteractive -File .\code\run_all.ps1 -Workspace .
```

The entry point uses only files shipped in this candidate. It validates the four
input hashes, clears generated `results/` and `figures/`, extracts the legacy XLS
files through read-only Excel COM, prepares the tidy data, reruns all models and
robustness checks, compiles the paper twice, performs tri-state consistency
verification, and creates a deterministic artifact manifest.

Python dependencies are pinned in `requirements.txt`. Excel COM and XeLaTeX are
system dependencies. The run does not call a project Skill, another phase, a
network service, or an earlier workspace.
