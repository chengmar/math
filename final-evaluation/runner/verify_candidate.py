from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_contract(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8-sig")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("non-JSON reproduction contract requires PyYAML") from error
        value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError("reproduction contract must be a mapping")
    return value


def safe_relative(value: object) -> Path:
    path = Path(str(value))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"declared output is not a safe relative path: {value}")
    return path


def copy_source_only(source: Path, scratch: Path, declared: set[Path]) -> list[str]:
    generated_roots = {path.parts[0] for path in declared if len(path.parts) > 1}
    undeclared_generated: list[str] = []
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_symlink():
            raise ValueError(f"symlink prohibited in candidate: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if relative in declared:
            continue
        if relative.parts[0] in generated_roots:
            undeclared_generated.append(relative.as_posix())
            continue
        target = scratch / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return undeclared_generated


def command_for_fixed_python(command: object, python: Path) -> list[str]:
    parts = shlex.split(str(command), posix=True)
    if not parts or Path(parts[0]).name.casefold() not in {"python", "python.exe", "python3", "python3.exe"}:
        raise ValueError("generate_all must be a direct Python command")
    prohibited = {"&&", "||", ";", "|", ">", ">>", "<"}
    if prohibited.intersection(parts):
        raise ValueError("shell composition is prohibited in generate_all")
    return [str(python), *parts[1:]]


def verify(args: argparse.Namespace) -> dict[str, object]:
    source = Path(args.source).resolve(strict=True)
    scratch = Path(args.scratch).resolve(strict=False)
    report_path = Path(args.report).resolve(strict=False)
    python = Path(args.python).resolve(strict=True)
    if not source.is_dir() or not python.is_file():
        raise ValueError("source must be a directory and python must be a file")
    if scratch.exists():
        raise FileExistsError(f"scratch destination already exists: {scratch}")
    if scratch == source or scratch in source.parents or source in scratch.parents:
        raise ValueError("source and scratch must be disjoint")

    contract_path = source / args.contract
    contract = load_contract(contract_path)
    declared_values = contract.get("declared_outputs")
    if not isinstance(declared_values, list) or not declared_values:
        raise ValueError("declared_outputs must be a non-empty list")
    declared_list = [safe_relative(value) for value in declared_values]
    if len(declared_list) != len(set(declared_list)):
        raise ValueError("declared_outputs contains duplicates")
    declared = set(declared_list)
    command = contract.get("generate_all")
    if not command:
        raise ValueError("generate_all is missing")

    missing_before = [path.as_posix() for path in declared_list if not (source / path).is_file()]
    source_hashes = {
        path.as_posix(): sha256_file(source / path)
        for path in declared_list
        if (source / path).is_file()
    }

    scratch.mkdir(parents=True)
    undeclared_generated = copy_source_only(source, scratch, declared)
    remaining_declared_before = [
        path.as_posix() for path in declared_list if (scratch / path).exists()
    ]
    argv = command_for_fixed_python(command, python)
    started = time.perf_counter()
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=scratch,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=args.timeout_seconds,
            check=False,
        )
        exit_code: int | None = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        exit_code = None
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
    elapsed = time.perf_counter() - started

    missing_after = [path.as_posix() for path in declared_list if not (scratch / path).is_file()]
    reproduced_hashes = {
        path.as_posix(): sha256_file(scratch / path)
        for path in declared_list
        if (scratch / path).is_file()
    }
    mismatched = [
        path.as_posix()
        for path in declared_list
        if path.as_posix() in source_hashes
        and path.as_posix() in reproduced_hashes
        and source_hashes[path.as_posix()] != reproduced_hashes[path.as_posix()]
    ]
    pdf_checks: dict[str, object] = {}
    for relative in declared_list:
        if relative.suffix.casefold() != ".pdf" or not (scratch / relative).is_file():
            continue
        try:
            from pypdf import PdfReader

            pages = len(PdfReader(scratch / relative).pages)
            pdf_checks[relative.as_posix()] = {
                "pages": pages,
                "bytes": (scratch / relative).stat().st_size,
                "pass": pages > 0 and (scratch / relative).stat().st_size > 0,
            }
        except Exception as error:  # pragma: no cover - diagnostic path
            pdf_checks[relative.as_posix()] = {"pass": False, "error": str(error)}

    passed = bool(
        not timed_out
        and exit_code == 0
        and not missing_before
        and not remaining_declared_before
        and not undeclared_generated
        and not missing_after
        and not mismatched
        and all(bool(item.get("pass")) for item in pdf_checks.values())
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "pass" if passed else "fail",
        "created_at": now_iso(),
        "source": str(source),
        "scratch": str(scratch),
        "report": str(report_path),
        "contract": str(contract_path),
        "python": str(python),
        "argv": argv,
        "timeout_seconds": args.timeout_seconds,
        "elapsed_seconds": elapsed,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "declared_count": len(declared_list),
        "missing_in_candidate_before_run": missing_before,
        "declared_present_in_source_only_scratch_before_run": remaining_declared_before,
        "undeclared_files_under_generated_roots": undeclared_generated,
        "missing_after_run": missing_after,
        "mismatched_hashes": mismatched,
        "source_hashes": source_hashes,
        "reproduced_hashes": reproduced_hashes,
        "pdf_checks": pdf_checks,
        "stdout": stdout,
        "stderr": stderr,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--source", required=True)
    result.add_argument("--scratch", required=True)
    result.add_argument("--report", required=True)
    result.add_argument("--python", required=True)
    result.add_argument("--contract", default="reproducibility.yaml")
    result.add_argument("--timeout-seconds", type=int, default=3600)
    return result


if __name__ == "__main__":
    payload = verify(parser().parse_args())
    print(json.dumps({
        "status": payload["status"],
        "report": payload["report"],
        "elapsed_seconds": payload["elapsed_seconds"],
        "mismatched_hashes": payload["mismatched_hashes"],
    }, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] == "pass" else 1)
