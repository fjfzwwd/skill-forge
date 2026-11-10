"""Static heuristic scanner: find crystallizable steps in a SKILL.md.

Pure standard library, no model calls. Output feeds `forge plan`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

BASH_FENCE = re.compile(r"^\s*```(?:bash|sh|shell|console|powershell)\b", re.I)
FENCE_END = re.compile(r"^\s*```\s*$")
FRONT = re.compile(r"^\s*---\s*$")

STRONG_CODE = re.compile(
    r"\b(grep|awk|jq|wc|sort|uniq|sed|cut|head|tail|find|sha256sum|stat|cat|python|node)\b|"
    r"(?:\||\$\s|\$\(|>\s*\d|exit\s+code)",
    re.I,
)
CN_CODE = re.compile(r"(统计|计算|校验|比较|计数|是否存在|超过|阈值|汇总|排序|筛选|解析|遍历|哈希|收集|读取|取得|列出)")
DECISION = re.compile(r"\bif\b.*\bthen\b|当.+?时|若.+?则|超过\s*\d+|>=\s*\d+|决策表|规则表", re.I)
MODEL = re.compile(
    r"(总结|撰写|评估|判断|权衡|命名|设计|解释|建议|询问|提问|语气|风格|取舍|AskUserQuestion|"
    r"summar|evaluat|judg|decide\s+whether|write\s+a\s+summary)",
    re.I,
)
KNOWLEDGE = re.compile(r"^(?:Use\s+when|参考|说明|背景|注|示例|Example|Note)\b|^#{1,3}\s", re.I)


def frontmatter_end(lines) -> int:
    """Return the 1-based last line of a REAL frontmatter block, else 0.

    Only a `---` on the very first line opens frontmatter. A horizontal rule
    later in the body must never swallow the rest of the document.
    """
    if not lines or not FRONT.match(lines[0].strip()):
        return 0
    for i in range(1, min(len(lines), 200)):
        if FRONT.match(lines[i].strip()):
            return i + 1
    return 0


def scan(text: str):
    """Return list of {line, text, score, owner, signals}."""
    lines = text.splitlines()
    fm_end = frontmatter_end(lines)
    in_fence = False
    out = []

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if i <= fm_end:
            continue
        if BASH_FENCE.match(line):
            in_fence = True
            continue
        if FENCE_END.match(line) and in_fence:
            in_fence = False
            continue
        if not line.strip():
            continue
        if KNOWLEDGE.search(line) and not in_fence:
            continue

        score, signals = 0, []
        if in_fence:
            score += 3
            signals.append("bash 代码块")
        if STRONG_CODE.search(line):
            score += 3
            signals.append("命令/管道")
        if CN_CODE.search(line):
            score += 2
            signals.append("确定性动词")
        if DECISION.search(line):
            score += 2
            signals.append("固定决策表")
        if MODEL.search(line):
            score -= 3
            signals.append("需模型判断")
        if KNOWLEDGE.search(line):
            score -= 4
            signals.append("知识性描述")
        if "?" in line or "？" in line:
            score -= 1
            signals.append("交互式提问")

        if score == 0:
            continue
        owner = "code" if score >= 2 else ("model" if score <= -2 else "review")
        out.append({
            "line": i,
            "text": line.strip()[:160],
            "score": score,
            "owner": owner,
            "signals": signals,
        })
    return out


def to_markdown(cands) -> str:
    rows = ["| 行号 | 得分 | 归属 | 信号 | 原文 |", "|---|---|---|---|---|"]
    for c in cands:
        rows.append("| L%d | %+d | %s | %s | %s |" % (
            c["line"], c["score"], c["owner"],
            "/".join(c["signals"]), c["text"].replace("|", "\\|")[:80],
        ))
    counts = {k: sum(1 for c in cands if c["owner"] == k) for k in ("code", "model", "review")}
    head = "候选步骤：code %d / model %d / review %d\n\n" % (
        counts["code"], counts["model"], counts["review"])
    return head + "\n".join(rows)


def write_scan(skill_dir: Path, text: str):
    build = skill_dir / "build"
    build.mkdir(parents=True, exist_ok=True)
    cands = scan(text)
    (build / "scan.json").write_text(
        json.dumps({"source": "SKILL.md", "candidates": cands}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (build / "scan.md").write_text(to_markdown(cands) + "\n", encoding="utf-8")
    return cands
