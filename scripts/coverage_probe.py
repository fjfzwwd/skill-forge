#!/usr/bin/env python3
"""Read-only coverage probe: how much of a real skill library can propose() map?

Never writes into the scanned skill directories. Writes one Markdown report.
Usage: python coverage_probe.py <skills_root> --out ../reports/propose-coverage.md
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyzer  # noqa: E402
import proposer  # noqa: E402
from batch import discover  # noqa: E402


def probe(root: Path):
    rows = []
    for d in discover(root):
        try:
            text = (d / "SKILL.md").read_text(encoding="utf-8", errors="ignore")
            cands = analyzer.scan(text)
            draft = proposer.propose(d, text, cands)
        except Exception as exc:
            rows.append({"skill": d.name, "error": str(exc)})
            continue
        steps = draft["steps"]
        code = [s for s in steps if s.get("owner") == "code"]
        conf = {k: sum(1 for s in steps if s.get("proposal", {}).get("confidence") == k)
                for k in ("high", "medium", "low")}
        rows.append({
            "skill": d.name,
            "candidates": sum(1 for c in cands if c["owner"] == "code"),
            "steps": len(code),
            "unmapped": len(draft["proposal"]["unmapped"]),
            "suggestions": len(draft["proposal"].get("suggestions", [])),
            "high": conf["high"], "medium": conf["medium"], "low": conf["low"],
            "decide": sum(1 for s in code if s.get("op") == "decide"),
        })
    return rows


def render(rows, root: Path) -> str:
    ok = [r for r in rows if "error" not in r]
    cand = sum(r["candidates"] for r in ok)
    mapped = sum(r["steps"] for r in ok)
    unmapped = sum(r["unmapped"] for r in ok)
    sugg = sum(r.get("suggestions", 0) for r in ok)
    from version import VERSION
    lines = [
        "# propose 覆盖率探针（真实技能库）", "",
        "- 范围：`%s`" % root.as_posix(),
        "- 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 生成器：skill-forge %s / IR v1.1（15 个内置 op）" % VERSION,
        "- 方式：**纯内存推断**，未向任何技能目录写入文件；用 `batch.discover` 保持发现口径一致。",
        "- 与 `batch` 报告的差别：`batch` 默认过滤 `code 候选 ≥ 1`，本探针包含全部（含 code=0 的知识型技能）。", "",
        "## 汇总", "", "| 指标 | 值 |", "|---|---|",
        "| 扫描 skill | %d |" % len(rows),
        "| 静态 code 候选 | %d |" % cand,
        "| 自动映射为 code step | %d |" % mapped,
        "| 明确列入未映射待审 | %d |" % unmapped,
        "| 需 L3 run_script 的建议（未自动放通） | %d |" % sugg,
        "| 候选 → 步骤名义覆盖率 | %.1f%% |" % (100 * mapped / max(cand, 1)), "",
        "## 逐技能", "",
        "| skill | code 候选 | 推断步骤 | 未映射 | L3 建议 | high | medium | low | decide |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if "error" in r:
            lines.append("| %s | ERROR | %s | | | | | | |" % (r["skill"], r["error"][:60]))
        else:
            lines.append("| %s | %d | %d | %d | %d | %d | %d | %d | %d |" % (
                r["skill"], r["candidates"], r["steps"], r["unmapped"],
                r.get("suggestions", 0),
                r["high"], r["medium"], r["low"], r["decide"]))
    lines += [
        "", "## 结论与口径", "",
        "- 正则候选数远大于可安全映射的内置 op 数：真实 SKILL.md 里含大量**示例命令**、说明性文字与框架特有语法，",
        "  把它们自动固化成流程正是「过度结晶」风险，因此 propose 采取宁缺毋滥策略。",
        "- **根因是表达边界，不是扫描器漏判**：IR v1.1 有 15 个内置 op，覆盖计数/匹配/收集/读文本/读 JSON/CSV/哈希/阈值/正则提取等只读动作；",
        "  真实技能里还有大量「调用 skill 自带的 Python/Node 脚本」「调用 API」「写文件」等动作，",
        "  这些被列入 `proposal.suggestions`（L3）或未映射，**不会自动放通**：避免把文档示例命令固化成流程。",
        "  确需执行时用 `forge fill` 显式补 `run_script` step（补丁不得自我提权），再由人审 "
        "`approve --yes --by <姓名> --enable-run-script` 放通并写 elevated_by。",
        "- 低覆盖率是 `review_status=PROPOSED` 门禁存在的实证理由：**propose 是填表助手，不是语义编译器**。",
        "- 未映射行会逐条列进 `build/review-sheet.md`，由人审决定补写或放弃；模型步骤与禁止动作由独立规则提取。",
        "- 本报告不测量 token / 遵循率收益，也不代表编译后的实际效果。", "",
        "## 实测结论（必须诚实说）", "",
        "- 本快照：%d 个静态候选 → %d 条自动映射（%.1f%%）、%d 条待审、%d 条可识别的 L3 脚本调用建议。" % (
            cand, mapped, 100 * mapped / max(cand, 1), unmapped, sugg),
        "- **扩到 15 个 op 后名义覆盖率并未显著提升**，这正是设计结果而非缺陷：新增 op 提供的是"
        "「人/Agent 有能力表达」的空间，不是「机器自动放通」的许可。真实技能里绝大多数候选是"
        "文档示例命令、说明性文字与框架特有语法，自动固化它们会造成过度结晶。",
        "- 要把某个技能真正编译，正确路径是 `propose` → 看 `review-sheet.md` 的未映射行与 L3 建议 → "
        "`forge fill` 显式补写 → `approve --yes --by [--enable-run-script]` 人审 → `compile`。"
        "覆盖率是安全策略的产物，不是 KPI；`examples/data-audit/` 是这条路径的完整可运行实例。",
        "- 技能库会动态安装新技能，以上数字均为生成时刻的快照，重跑本命令即可刷新。", "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="只读 propose 覆盖率探针")
    ap.add_argument("root")
    ap.add_argument("--out", default="../reports/propose-coverage.md")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if not root.is_dir():
        print("目录不存在：%s" % root)
        return 2
    rows = probe(root)
    out = Path(a.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows, root), encoding="utf-8")
    ok = [r for r in rows if "error" not in r]
    print("扫描 %d 个 skill，code 候选 %d，自动映射 %d 步，未映射 %d 行 → %s" % (
        len(rows), sum(r["candidates"] for r in ok), sum(r["steps"] for r in ok),
        sum(r["unmapped"] for r in ok), out.as_posix()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
