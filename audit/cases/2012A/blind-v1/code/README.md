# Reproduction

Run from PowerShell in this solve workspace:

```powershell
& .\code\run_all.ps1 -Workspace '<RUNTIME_ROOT>\2012A\workspaces\solve'
```

The pipeline checks the solve lock, extracts all legacy XLS sheets through read-only Excel COM, parses and audits the tables, reruns every model and figure, compiles the XeLaTeX paper twice, and performs internal consistency checks. It writes only below the current solve workspace.

Individual entry points are `extract_xls.ps1`, `prepare_data.py`, `analyze.py`, and `verify.py`. Python package versions are pinned in `requirements.txt`; Excel COM and XeLaTeX are system dependencies.
