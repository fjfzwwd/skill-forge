"""Run the generated runner and compare against a committed baseline.

A missing baseline is NEVER created implicitly. Only an explicit
`--update-baseline` writes one; otherwise verify fails loudly. This prevents a
broken first run from silently becoming the "expected" output.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

META_KEYS = {"ELAPSED_MS", "STEPS"}


def parse_kv(stdout: str) -> dict:
    out = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("===") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def run(runner: Path, root=None, timeout=60):
    cmd = [sys.executable, str(runner)]
    if root:
        cmd += ["--root", str(root)]
    return subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def _case(case_id, passed, **extra):
    payload = {"case_id": case_id, "passed": bool(passed)}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def verify(build: Path, update=False, fmt="text", root=None) -> int:
    runner = build / "scripts" / "crystallized.py"
    base_file = build / "fixtures" / "baseline.json"
    jsonl = fmt == "jsonl"
    if not runner.is_file():
        msg = "FAIL 未找到生成脚本：%s（先运行 compile）" % runner
        print(_case("crystallized-runner", False, detail=msg) if jsonl else msg)
        return 2
    if not update and not base_file.is_file():
        msg = "NO_BASELINE 缺少回归基线；先运行 `forge verify <dir> --update-baseline`"
        print(_case("crystallized-runner", False, detail=msg, diffs=[]) if jsonl else msg)
        return 1

    try:
        proc = run(runner, root)
    except subprocess.TimeoutExpired:
        msg = "FAIL 脚本执行超过 60 秒"
        print(_case("crystallized-runner", False, detail=msg, diffs=[]) if jsonl else msg)
        return 1
    except OSError as exc:
        msg = "FAIL 无法启动脚本：%s" % exc
        print(_case("crystallized-runner", False, detail=msg, diffs=[]) if jsonl else msg)
        return 1

    kv = parse_kv(proc.stdout)
    observed = {k: v for k, v in kv.items() if k not in META_KEYS}

    if not observed:
        msg = "FAIL 脚本没有产生结构化输出"
        if jsonl:
            print(_case("crystallized-runner", False, detail=msg, diffs=[]))
        else:
            print(msg)
            print(proc.stdout.strip())
            print(proc.stderr.strip())
        return 1
    if kv.get("STATUS") == "ERROR":
        msg = "FAIL 脚本返回 STATUS=ERROR：%s" % kv.get("ERROR", "")
        print(_case("crystallized-runner", False, detail=msg, outputs=observed, diffs=[])
              if jsonl else msg)
        return 1
    if proc.returncode != 0:
        msg = "FAIL 脚本退出码 %d" % proc.returncode
        if jsonl:
            print(_case("crystallized-runner", False, detail=msg, outputs=observed, diffs=[]))
        else:
            print(msg)
            print(proc.stderr.strip())
        return 1

    base_file.parent.mkdir(parents=True, exist_ok=True)
    if update:
        base_file.write_text(json.dumps({"keys": observed}, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        msg = "BASELINE 已写入 %s" % base_file
        if jsonl:
            print(_case("crystallized-runner", True, detail=msg, outputs=observed, diffs=[]))
        else:
            print(msg)
            print(json.dumps(observed, ensure_ascii=False))
        return 0

    try:
        expected = json.loads(base_file.read_text(encoding="utf-8-sig")).get("keys", {})
    except (ValueError, OSError) as exc:
        msg = "FAIL 基线文件损坏：%s" % exc
        print(_case("crystallized-runner", False, detail=msg, diffs=[]) if jsonl else msg)
        return 1
    diffs = []
    for k in sorted(set(expected) | set(observed)):
        if expected.get(k) != observed.get(k):
            diffs.append((k, expected.get(k), observed.get(k)))

    passed = not diffs
    if jsonl:
        print(_case("crystallized-runner", passed,
                    detail=("PASS 结构化输出与基线一致" if passed else "FAIL 与基线不一致"),
                    outputs=observed, diffs=diffs))
    else:
        print("PASS 结构化输出与基线一致（%d 个键）" % len(observed) if passed
              else "FAIL 与基线不一致（%d 处差异）" % len(diffs))
        for k, exp, got in diffs:
            print("  %s: 期望 %r，实际 %r" % (k, exp, got))
        if passed:
            print(json.dumps(observed, ensure_ascii=False, indent=2))
    return 0 if passed else 1
