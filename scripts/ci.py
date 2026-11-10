"""CI gate: aggregate watch + verify for every compiled skill in a tree.

Emits one JSONL case per check so external eval runners can consume it.
Never updates a baseline and never writes into skill directories.
"""
from __future__ import annotations

import json
from pathlib import Path

import core
from batch import discover, approved_ir
from version import VERSION

GENERATOR = "skill-forge ci %s" % VERSION


def collect(root: Path, strict=False):
    cases = []
    for d in discover(root):
        if not (d / "build" / "crystallize.lock.json").is_file():
            continue
        name = d.name
        rc, msg = core.watch_skill(d)
        cases.append({
            "case_id": name + "/watch", "passed": rc == 0,
            "check": "source+artifact drift", "detail": msg,
            "skill": d.as_posix(), "generator": GENERATOR,
        })
        base = d / "build" / "fixtures" / "baseline.json"
        if not base.is_file():
            cases.append({
                "case_id": name + "/verify", "passed": False,
                "check": "regression baseline", "detail": "NO_BASELINE 缺少回归基线，CI 不自动创建",
                "skill": d.as_posix(), "generator": GENERATOR,
            })
            continue
        vrc, out = core.verify_skill(d, False, "jsonl", False)
        payload = {}
        for line in reversed(out.splitlines()):
            if line.strip().startswith("{"):
                try:
                    payload = json.loads(line)
                    break
                except Exception:
                    continue
        cases.append({
            "case_id": name + "/verify", "passed": vrc == 0 and bool(payload.get("passed", True)),
            "check": "regression baseline",
            "detail": ("PASS 结构化输出与基线一致" if vrc == 0 else out.strip()[:200]),
            "outputs": payload.get("outputs", {}), "diffs": payload.get("diffs", []),
            "skill": d.as_posix(), "generator": GENERATOR,
        })

    if strict:
        for d in discover(root):
            if (d / "build" / "ir.proposed.json").is_file() and not approved_ir(d):
                cases.append({
                    "case_id": d.name + "/review", "passed": False,
                    "check": "unreviewed proposal",
                    "detail": "UNREVIEWED 存在机器草案但无人审签发 IR；禁止合入",
                    "skill": d.as_posix(), "generator": GENERATOR,
                })
    return cases


def cmd_ci(a) -> int:
    root = Path(a.root).resolve()
    if not root.is_dir():
        print("目录不存在：%s" % root)
        return 2
    cases = collect(root, a.strict)
    compiled = sum(1 for c in cases if c["case_id"].endswith("/watch"))
    if compiled == 0 and not a.allow_empty:
        print("NO_COMPILED_SKILL 未找到已编译 skill（加 --allow-empty 可视为通过）")
        return 2
    failed = [c for c in cases if not c["passed"]]

    if a.format == "jsonl":
        for c in cases:
            print(json.dumps(c, ensure_ascii=False))
    else:
        print("skill-forge CI · %s · strict=%s" % (root.as_posix(), a.strict))
        print("| case | 检查 | 结果 | 说明 |")
        print("|---|---|---|---|")
        for c in cases:
            print("| %s | %s | %s | %s |" % (
                c["case_id"], c["check"], "PASS" if c["passed"] else "FAIL",
                str(c["detail"]).replace("|", "\\|")[:100]))
        print("\n%d 个已编译 skill / %d 项检查 / %d 项失败" % (
            compiled, len(cases), len(failed)))
        print("GATE " + ("PASS" if not failed else "FAIL"))
    return 1 if failed else 0


def register(sub):
    p = sub.add_parser("ci", help="CI 门禁：聚合 watch + verify，输出 jsonl cases")
    p.add_argument("root", help="技能库或项目根目录")
    p.add_argument("--format", choices=["text", "jsonl"], default="text")
    p.add_argument("--strict", action="store_true", help="未人审的机器草案也算失败")
    p.add_argument("--allow-empty", action="store_true", help="没有已编译 skill 时仍返回 0")
    p.set_defaults(func=cmd_ci)
