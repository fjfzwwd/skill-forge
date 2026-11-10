"""Static portability checks for shipped Python code.

Local smoke runs on Windows only prove Windows. This module does not pretend
to execute on Linux/macOS; it checks the *code patterns* that most often break
there: implicit encodings, shell=True, OS-specific path separators, and
subprocess calls without an explicit encoding or timeout.

All checks are AST-based, so comments and regex literals do not cause noise.
`forge doctor` reports them as a single gate.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SKIP_PARTS = {"__pycache__", "build", "dist", "reports", ".git", ".workbuddy"}
WIN_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:\\[A-Za-z0-9_.-]+)+$")
IO_CALLS = {"open", "read_text", "write_text", "read_bytes", "write_bytes"}
BLOCKED_CALLS = {"system", "popen", "spawnl", "spawnv", "eval", "execfile"}


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _kw(call: ast.Call, name: str) -> bool:
    return any(k.arg == name for k in call.keywords)


def check_file(path: Path) -> list:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
    except (OSError, SyntaxError) as exc:
        return ["%s: 无法解析 (%s)" % (path.name, exc)]
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if WIN_PATH.match(node.value):
                issues.append("L%d 硬编码反斜杠路径 %r（用 Path / as_posix）"
                              % (node.lineno, node.value))
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in BLOCKED_CALLS:
            issues.append("L%d 调用 %s（不可移植/不安全）" % (node.lineno, name))
        if name in ("open", "read_text", "write_text") and not _kw(node, "encoding"):
            issues.append("L%d %s() 未显式指定 encoding" % (node.lineno, name))
        if any(k.arg == "shell" and isinstance(k.value, ast.Constant)
               and k.value.value is True for k in node.keywords):
            issues.append("L%d subprocess 使用 shell=True" % node.lineno)
        # Only real subprocess.run calls, not our own `run(...)` helpers.
        is_subprocess_run = (isinstance(node.func, ast.Attribute)
                             and node.func.attr == "run"
                             and isinstance(node.func.value, ast.Name)
                             and node.func.value.id == "subprocess")
        if is_subprocess_run and not _kw(node, "timeout"):
            # subprocess.run without a timeout can hang forever on a slow CI.
            issues.append("L%d subprocess.run 缺少 timeout" % node.lineno)
    return issues


def scan(root: Path) -> dict:
    """Return {file: [issues]} for every shipped .py file."""
    out = {}
    for p in sorted(root.rglob("*.py")):
        if any(part in SKIP_PARTS for part in p.relative_to(root).parts):
            continue
        problems = check_file(p)
        if problems:
            out[p.relative_to(root).as_posix()] = problems
    return out
