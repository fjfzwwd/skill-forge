#!/usr/bin/env python3
"""skill-forge CLI.

Zero third-party dependencies. Python 3.9+. Windows / macOS / Linux.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
import analyzer  # noqa: E402
import batch as batchmod  # noqa: E402
import ci as cimod  # noqa: E402
import stability  # noqa: E402
import release  # noqa: E402
from version import VERSION  # noqa: E402


def _emit(lines) -> None:
    if isinstance(lines, str):
        print(lines)
    else:
        for line in lines:
            print(line)


def cmd_scan(a) -> int:
    d = core.skill_dir(a.skill_dir)
    cands = analyzer.write_scan(d, core.read_skill(d))
    print(analyzer.to_markdown(cands))
    print("\n已写入 %s" % (d / "build" / "scan.json"))
    return 0


def cmd_plan(a) -> int:
    d = core.skill_dir(a.skill_dir)
    text = core.read_skill(d)
    cands = analyzer.write_scan(d, text)
    codelines = [c for c in cands if c["owner"] == "code"]
    draft = {
        "version": "1",
        "target": core.IR.slugify_target(d.name),
        "source_sha256": "auto",
        "allow_shell": False,
        "allow_run_script": False,
        "allow_plugins": False,
        "status": "DRAFT：请人工或由 Agent 填写 steps 后另存为 ir.json，再执行 compile",
        "candidates": [
            {"line": c["line"], "source": "SKILL.md:L%d" % c["line"],
             "score": c["score"], "text": c["text"]}
            for c in codelines
        ],
        "steps": [],
        "gates": ["read_only"],
        "prohibitions": [],
        "kept_for_model": [],
        "hint": "推荐用 `forge propose` 自动填 steps，再用 `forge fill` 补 L3 动作",
    }
    out = d / "build" / "ir.draft.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("发现 %d 个可结晶候选 / %d 个需模型保留 / %d 个待审" % (
        len(codelines),
        sum(1 for c in cands if c["owner"] == "model"),
        sum(1 for c in cands if c["owner"] == "review"),
    ))
    print("草案已写入 %s" % out)
    print("下一步：`forge propose %s` 自动推断，或手工填写 steps" % d.name)
    return 0


def cmd_propose(a) -> int:
    d = core.skill_dir(a.skill_dir)
    rc, lines, _ = core.propose_skill(d)
    _emit(lines)
    return rc


def cmd_fill(a) -> int:
    d = core.skill_dir(a.skill_dir)
    rc, lines, _ = core.fill_skill(d, Path(a.patch).resolve(), a.by)
    _emit(lines)
    return rc


def cmd_approve(a) -> int:
    d = core.skill_dir(a.skill_dir)
    rc, lines = core.approve_skill(d, a.ir, a.out, a.by, a.yes,
                                   a.enable_shell, a.enable_run_script,
                                   a.enable_plugins)
    # Without --yes this is strictly read-only: the sheet was written by
    # `propose`/`fill`, so never overwrite it here.
    _emit(lines)
    return rc


def cmd_compile(a) -> int:
    d = core.skill_dir(a.skill_dir)
    rc, lines = core.compile_skill(d, a.ir, a.force)
    _emit(lines)
    return rc


def cmd_verify(a) -> int:
    d = core.skill_dir(a.skill_dir)
    rc, out = core.verify_skill(d, a.update_baseline, a.format,
                                a.root_from_skill, a.record)
    print(out)
    return rc


def cmd_watch(a) -> int:
    d = core.skill_dir(a.skill_dir)
    rc, out = core.watch_skill(d)
    print(out)
    return rc


def cmd_guard(a) -> int:
    d = core.skill_dir(a.skill_dir)
    status, reason, detail = stability.check(d, min_runs=a.min_runs)
    print("GUARD %s %s" % (status, reason))
    if a.format == "json":
        print(json.dumps({"status": status, "reason": reason, "detail": detail},
                         ensure_ascii=False, indent=2))
    return 1 if status == stability.NOT_READY else 0


def cmd_measure(a) -> int:
    d = core.skill_dir(a.skill_dir)
    p = stability.measure(d, a.runs, str(d) if a.root_from_skill else "", a.record)
    if p is None:
        print("缺少生成脚本：先 `forge compile %s`" % d.name)
        return 2
    out = Path(a.out).resolve() if a.out else d / "build" / "measurement.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = out.with_suffix(".md")
    md.write_text(stability.render_measurement(p), encoding="utf-8")
    print("实测 %d 次，成功 %d 次；形状稳定=%s；耗时中位数 %d ms；输出 %d B" % (
        p["requested_runs"], p["successful_runs"], p["shape_stable"],
        p["elapsed_ms_median"], p["output_bytes_median"]))
    print("报告：%s / %s" % (out, md))
    print("口径：" + p["scope_note"])
    # Keep the estimate report deterministic (it is inside the drift lock) and
    # write measured data to a separate, unlocked file.
    _, rlines = core.report_skill(d, str(out))
    for line in rlines:
        print(line)
    return 0 if p["successful_runs"] == p["requested_runs"] else 1


def cmd_report(a) -> int:
    d = core.skill_dir(a.skill_dir)
    rc, lines = core.report_skill(d, a.measurement)
    _emit(lines)
    return rc


def cmd_bench(a) -> int:
    import bench
    d = core.skill_dir(a.skill_dir)
    try:
        fixtures = bench.load_fixtures(d, a.fixtures)
        p = bench.run(d, core.read_skill(d), fixtures, a.runs, a.timeout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("ERROR bench 无法执行：%s" % exc)
        return 2
    build = d / "build"
    minimal = (build / "SKILL.md").read_text(encoding="utf-8-sig")
    out = build / "benchmark.json"
    out.write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(bench.render_markdown(p), encoding="utf-8")
    cases = bench.eval_cases(p, d, fixtures, core.read_skill(d), minimal)
    (build / "bench-cases.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")

    if a.format == "jsonl":
        for c in cases or [{"case_id": p["target"] + "/no-fixtures",
                            "passed": p["passed"], "check": "determinism",
                            "detail": "NOT_DECLARED 未声明用例"}]:
            print(json.dumps(c, ensure_ascii=False))
    else:
        v = p["prompt"]
        det, conf = p["determinism"], p["conformance"]
        print("BENCH %s · %s" % (p["target"], "PASS" if p["passed"] else "FAIL"))
        print("  prompt 字符：%d → %d（%+.0f%%）；行：%d → %d" % (
            v["before"]["chars"], v["after"]["chars"],
            100 * v["delta"]["chars"] / max(v["before"]["chars"], 1),
            v["before"]["lines"], v["after"]["lines"]))
        print("  确定性：%d/%d 次成功，形状稳定=%s，耗时中位数 %d ms" % (
            det["successful_runs"], det["requested_runs"],
            det["shape_stable"], det["elapsed_ms_median"]))
        print("  用例符合度：%s" % (
            "NOT_DECLARED（未声明 fixtures）" if not conf["declared"] else
            "%d/%d PASS" % (conf["passed"], conf["cases_total"])))
        print("  tokenizer：%s" % v["tokenizer"]["method"])
        print("  报告：%s / %s" % (out, out.with_suffix(".md")))
        print("  eval 适配：%s（%d cases）" % (build / "bench-cases.jsonl", len(cases)))
        print("  口径：" + p["scope_note"])
    return 0 if p["passed"] else 1


def cmd_smoke(a) -> int:
    import smoke
    rc, lines = smoke.run(project_root(a.root), use_zip=not a.no_zip,
                          timeout=a.timeout)
    _emit(lines)
    return rc


def cmd_rollback(a) -> int:
    d = core.skill_dir(a.skill_dir)
    backup = d / "build" / "backup" / "SKILL.md"
    if not backup.is_file():
        print("没有备份：%s" % backup)
        return 2
    if not a.yes:
        print("将用备份覆盖 %s。确认请加 --yes" % (d / "SKILL.md"))
        return 1
    shutil.copy2(backup, d / "SKILL.md")
    print("已恢复源 SKILL.md")
    return 0


def project_root(raw) -> Path:
    """Accept either the skill root or its scripts/ subdirectory.

    `cd scripts && forge doctor .` is a natural mistake; resolve it instead of
    reporting 30 missing files.
    """
    p = Path(raw).resolve() if raw else Path(__file__).resolve().parent.parent
    if not (p / "SKILL.md").is_file() and (p / "forge.py").is_file():
        p = p.parent
    return p


def cmd_doctor(a) -> int:
    rc, lines = release.doctor(project_root(a.root))
    _emit(lines)
    return rc


def cmd_package(a) -> int:
    root = project_root(a.root)
    out = Path(a.out).resolve() if a.out else None
    rc, lines, out = release.package(root, out)
    _emit(lines)
    if a.open and out:
        print("发行包：%s" % out)
    return rc


def cmd_selftest(a) -> int:
    import unittest
    root = project_root(a.root)
    tests = root / "tests"
    if not tests.is_dir():
        print("找不到测试目录：%s" % tests)
        return 2
    suite = unittest.defaultTestLoader.discover(str(tests), pattern="test_*.py",
                                                top_level_dir=str(root))
    result = unittest.TextTestRunner(verbosity=2 if a.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="forge",
                                 description="Skill 编译／结晶器 CLI · v%s" % VERSION)
    ap.add_argument("--version", action="version", version="skill-forge " + VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="静态扫描可结晶步骤")
    p.add_argument("skill_dir")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("plan", help="生成空 IR 草案（M0 兼容）")
    p.add_argument("skill_dir")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("propose", help="自动推断 IR 草案（不可直接编译）")
    p.add_argument("skill_dir")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("fill", help="把 Agent 补丁合并进草案（仍不可直接编译）")
    p.add_argument("skill_dir")
    p.add_argument("--patch", required=True, help="补丁 JSON，格式见 templates/agent-patch.json")
    p.add_argument("--by", default="agent", help="补丁来源标识")
    p.set_defaults(func=cmd_fill)

    p = sub.add_parser("approve", help="人审签发 IR 草案")
    p.add_argument("skill_dir")
    p.add_argument("--ir", help="草案路径，默认 <dir>/build/ir.proposed.json")
    p.add_argument("--out", help="正式 IR 输出路径，默认 <dir>/build/ir.json")
    p.add_argument("--by", default="human", help="审阅者标识，写入 approved_by")
    p.add_argument("--yes", action="store_true", help="确认清单无误并签发；不加则只打印清单")
    p.add_argument("--enable-shell", action="store_true",
                   help="人工显式放通 shell（L2），仅在 IR 确有 shell 步骤时可用")
    p.add_argument("--enable-run-script", action="store_true",
                   help="人工显式放通 run_script（L3），仅在 IR 确有该步骤时可用")
    p.add_argument("--enable-plugins", action="store_true",
                   help="人工显式放通 plugin_*（L3），仅在 IR 确有插件步骤时可用")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("compile", help="人审 IR → 生成脚本/精简文档/报告")
    p.add_argument("skill_dir")
    p.add_argument("--ir", help="IR 路径，默认 <skill_dir>/build/ir.json")
    p.add_argument("--force", action="store_true",
                   help="显式覆盖源漂移与 NOT_READY 护栏，写入 build/ 留痕")
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("verify", help="运行生成脚本并比对基线（缺基线不会自动创建）")
    p.add_argument("skill_dir")
    p.add_argument("--update-baseline", action="store_true")
    p.add_argument("--format", choices=["text", "jsonl"], default="text")
    p.add_argument("--root-from-skill", action="store_true", help="显式把 skill 目录作为数据根")
    p.add_argument("--record", action="store_true", help="把本次输出形状记入 runs.jsonl 供护栏使用")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("watch", help="检测源文档/产物漂移")
    p.add_argument("skill_dir")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("guard", help="检查流程形状是否稳定（NOT_READY 护栏）")
    p.add_argument("skill_dir")
    p.add_argument("--min-runs", type=int, default=3)
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=cmd_guard)

    p = sub.add_parser("measure", help="实测耗时/输出字节/形状稳定性")
    p.add_argument("skill_dir")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--record", action="store_true", help="采样写入 runs.jsonl")
    p.add_argument("--out", help="JSON 报告路径，默认 <dir>/build/measurement.json")
    p.add_argument("--root-from-skill", action="store_true")
    p.set_defaults(func=cmd_measure)

    p = sub.add_parser("report", help="把实测数据合并进不进锁的报告")
    p.add_argument("skill_dir")
    p.add_argument("--measurement", help="measurement.json 路径，默认 <dir>/build/measurement.json")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("bench", help="编译前后离线对照 + 外部 eval runner 适配")
    p.add_argument("skill_dir")
    p.add_argument("--fixtures", help="声明式用例，默认 <dir>/bench-fixtures.json")
    p.add_argument("--runs", type=int, default=3, help="确定性采样次数")
    p.add_argument("--timeout", type=int, default=60, help="单用例超时秒数")
    p.add_argument("--format", choices=["text", "jsonl"], default="text")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("smoke", help="干净目录一键端到端验收（含发行包解压）")
    p.add_argument("root", nargs="?", help="技能根目录，默认自动推断")
    p.add_argument("--no-zip", action="store_true", help="不用 dist/ 的 zip，改为复制发行清单")
    p.add_argument("--timeout", type=int, default=180, help="单步超时秒数")
    p.set_defaults(func=cmd_smoke)

    p = sub.add_parser("rollback", help="从备份恢复源 SKILL.md")
    p.add_argument("skill_dir")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("doctor", help="上架前自检：语法/元数据/链接/版本/路径泄漏")
    p.add_argument("root", nargs="?")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("package", help="打包可上传的技能目录为 zip")
    p.add_argument("root", nargs="?")
    p.add_argument("--out", help="输出目录，默认 <root>/dist")
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("selftest", help="运行内置 unittest 套件")
    p.add_argument("root", nargs="?", help="技能根目录，默认自动推断")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_selftest)

    batchmod.register(sub)
    cimod.register(sub)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
