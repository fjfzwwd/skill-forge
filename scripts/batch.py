"""Batch discovery: rank a whole skill library by crystallization value.

Read-only by default. --propose and --compile-approved are explicit opt-ins
because they write into the target skill directories.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import analyzer
import core
from version import VERSION

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", "build", "dist",
    ".venv", "venv", "site-packages", ".cache", ".idea", ".vscode",
    ".workbuddy", "vendor", "target",
}
GENERATOR = "skill-forge batch %s" % VERSION


def discover(root: Path):
    """Yield directories containing a source SKILL.md (never a build/ copy)."""
    seen = set()
    for p in sorted(root.rglob("SKILL.md")):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        d = p.parent.resolve()
        if d in seen:
            continue
        seen.add(d)
        yield d


def status_of(d: Path) -> str:
    b = d / "build"
    if (b / "crystallize.lock.json").is_file():
        return "COMPILED"
    if (b / "ir.json").is_file():
        return "IR_APPROVED"
    if (b / "ir.proposed.json").is_file():
        return "PROPOSED"
    return "UNTOUCHED"


def approved_ir(d: Path):
    """Return the path of a human-approved IR, or None."""
    p = d / "build" / "ir.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("review_status") in (None, "APPROVED"):
        return p
    return None


def analyze_one(d: Path, root: Path, freq: dict, excludes) -> dict:
    text = core.read_skill(d)
    cands = analyzer.scan(text)
    counts = {k: sum(1 for c in cands if c["owner"] == k)
              for k in ("code", "model", "review")}
    name = d.name
    if any(x.lower() in name.lower() for x in excludes):
        return None
    f = float(freq.get(name, freq.get(d.as_posix(), 1)) or 1)
    priority = round((counts["code"] * 3 + counts["review"] * 1
                      - counts["model"] * 0.5) * f, 1)
    st = status_of(d)
    action = {
        "COMPILED": "已编译，接入 ci 门禁",
        "IR_APPROVED": "可直接 compile / 批量编译",
        "PROPOSED": "待人审：forge approve --yes",
        "UNTOUCHED": "forge propose 生成草案",
    }[st]
    if counts["code"] == 0:
        action = "低信号：纯判断/知识型，不建议结晶"
    return {
        "skill": name,
        "path": d.as_posix(),
        "relative": d.relative_to(root).as_posix() if _is_under(d, root) else d.as_posix(),
        "lines": len(text.splitlines()),
        "code": counts["code"],
        "model": counts["model"],
        "review": counts["review"],
        "frequency": f,
        "priority": priority,
        "est_roundtrips_saved": int(counts["code"] * f),
        "status": st,
        "action": action,
        "top_lines": [c["line"] for c in cands if c["owner"] == "code"][:5],
    }


def _is_under(d: Path, root: Path) -> bool:
    try:
        d.relative_to(root)
        return True
    except ValueError:
        return False


def render_report(rows, root: Path, opts: dict) -> str:
    total = len(rows)
    compiled = sum(1 for r in rows if r["status"] == "COMPILED")
    approved = sum(1 for r in rows if r["status"] == "IR_APPROVED")
    proposed = sum(1 for r in rows if r["status"] == "PROPOSED")
    untouched = sum(1 for r in rows if r["status"] == "UNTOUCHED")
    code_total = sum(r["code"] for r in rows)
    saved = sum(r["est_roundtrips_saved"] for r in rows)
    table = ["| # | skill | 行 | code/model/review | 状态 | 频率 | 优先级 | 省往返/周期 | 建议 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        table.append("| %d | `%s` | %d | %d/%d/%d | %s | %g | %.1f | %d | %s |" % (
            i, r["skill"], r["lines"], r["code"], r["model"], r["review"],
            r["status"], r["frequency"], r["priority"], r["est_roundtrips_saved"], r["action"],
        ))
    return "\n".join([
        "# 技能库结晶优先级报告", "",
        "- 扫描根目录：`%s`" % root.as_posix(),
        "- 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 生成器：%s" % GENERATOR,
        "- 过滤：code 候选 ≥ %d" % opts["min_code"], "",
        "## 概览", "",
        "| 指标 | 值 |", "|---|---|",
        "| 扫描到 skill | %d |" % total,
        "| 已编译（COMPILED） | %d |" % compiled,
        "| 已签发 IR（IR_APPROVED） | %d |" % approved,
        "| 机器草案待人审（PROPOSED） | %d |" % proposed,
        "| 未处理（UNTOUCHED） | %d |" % untouched,
        "| code 候选步骤合计 | %d |" % code_total,
        "| 估算每周期可省工具往返 | %d |" % saved, "",
        "## 优先级排序", "", *table, "",
        "## 口径与限制", "",
        "- `优先级 = (code×3 + review×1 − model×0.5) × 调用频率`，是启发式排序，**不是实测收益**。",
        "- `省往返/周期 = code 候选数 × 调用频率` 的粗估；未提供 `--frequency` 时频率取 1。",
        "- `code/model/review` 来自正则启发式扫描，会漏判也会误判，**必须逐条人审**。",
        "- 本报告默认只读。`--propose` / `--compile-approved` 会写入目标技能目录，需显式开启。",
        "- 编译只对**人审签发过**的 IR 生效；机器草案永远不能自动编译。", "",
        "## 建议动作顺序", "",
        "1. 对优先级最高且 `code ≥ 3` 的 skill 跑 `forge propose <dir>`。",
        "2. 打开 `build/review-sheet.md` 逐条核对，再 `forge approve <dir> --yes --by <姓名>`。",
        "3. `forge compile <dir>` → `forge verify <dir> --update-baseline` → `forge watch <dir>`。",
        "4. 在 CI 里跑 `forge ci <root> --strict`，锁住源文档与产物漂移。", "",
    ]) + "\n"


def cmd_batch(a) -> int:
    root = Path(a.root).resolve()
    if not root.is_dir():
        print("目录不存在：%s" % root)
        return 2
    freq = {}
    if a.frequency:
        try:
            freq = json.loads(Path(a.frequency).read_text(encoding="utf-8"))
        except Exception as exc:
            print("频率文件无法解析：%s" % exc)
            return 2
    rows = []
    for d in discover(root):
        r = analyze_one(d, root, freq, a.exclude or [])
        if r is None or r["code"] < a.min_code:
            continue
        if a.propose and r["status"] == "UNTOUCHED":
            _, _, _ = core.propose_skill(d)
            r["status"] = "PROPOSED"
            r["action"] = "已生成草案，待人审：forge approve --yes"
        if a.compile_approved:
            p = approved_ir(d)
            if p:
                rc, lines = core.compile_skill(d, p)
                # A freshly compiled skill has no baseline. Establish it
                # explicitly (this is an opt-in writing mode), then verify.
                if rc == 0:
                    core.verify_skill(d, True, "text", False)
                vrc, vout = core.verify_skill(d, False, "jsonl", False)
                r["compile"] = "OK" if rc == 0 else "GATE_FAIL"
                r["verify"] = "PASS" if vrc == 0 else "FAIL"
                if rc != 0:
                    r["compile_error"] = " / ".join(
                        x for x in lines if x.startswith("ERROR") or x.startswith("GUARD"))[:300]
                if vrc != 0:
                    r["verify_error"] = vout.strip()[:200]
                r["status"] = "COMPILED" if rc == 0 else r["status"]
                r["action"] = ("已批量编译 + 回归，接入 ci 门禁" if rc == 0 and vrc == 0
                               else "批量编译/回归失败，查看日志")
        rows.append(r)
    rows.sort(key=lambda r: (-r["priority"], r["skill"]))

    out_dir = Path(a.out).resolve() if a.out else Path.cwd() / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = a.name or "batch"
    payload = {
        "generator": GENERATOR,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": root.as_posix(),
        "min_code": a.min_code,
        "propose": a.propose,
        "compile_approved": a.compile_approved,
        "total": len(rows),
        "skills": rows,
    }
    (out_dir / (prefix + ".json")).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / (prefix + "-report.md")).write_text(
        render_report(rows, root, {"min_code": a.min_code}), encoding="utf-8")

    if a.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("扫描 %d 个 skill（code ≥ %d），报告：%s" % (
            len(rows), a.min_code, (out_dir / (prefix + "-report.md")).as_posix()))
        print("| # | skill | code/mod/rev | 状态 | 优先级 | 建议 |")
        print("|---|---|---|---|---|---|")
        for i, r in enumerate(rows[:20], 1):
            print("| %d | %s | %d/%d/%d | %s | %.1f | %s |" % (
                i, r["skill"], r["code"], r["model"], r["review"],
                r["status"], r["priority"], r["action"]))
        if len(rows) > 20:
            print("… 其余 %d 个见报告" % (len(rows) - 20))
    return 0


def register(sub):
    p = sub.add_parser("batch", help="批量扫描技能库并输出优先级报告（默认只读）")
    p.add_argument("root", help="技能库根目录")
    p.add_argument("--out", help="报告输出目录，默认 ./reports")
    p.add_argument("--name", help="报告文件名前缀，默认 batch")
    p.add_argument("--min-code", type=int, default=1, help="只列 code 候选 ≥ N 的 skill")
    p.add_argument("--frequency", help="JSON: skill 名 → 调用频次，用于加权排序")
    p.add_argument("--exclude", action="append", default=[], help="按名称排除，可重复")
    p.add_argument("--propose", action="store_true", help="为未处理 skill 生成 IR 草案（会写入目标目录）")
    p.add_argument("--compile-approved", action="store_true",
                   help="批量编译已人审签发 IR 的 skill（会写入目标目录）")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=cmd_batch)
