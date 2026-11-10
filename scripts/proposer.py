"""Heuristic IR proposer: draft a reviewable Crystallize IR from SKILL.md.

Pure standard library. Never calls a model. Every inferred step keeps its
source line, a confidence label and the exact text it was inferred from, so a
human can audit the guess instead of trusting it.

A proposal is NOT compilable: ir.validate() rejects review_status=PROPOSED.
`forge approve` is the explicit human action that turns it into a real IR.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ir import sha256_text, slugify_target
from version import VERSION
import analyzer
import oppack

INLINE = re.compile(r"`([^`]+)`")
NUM_PREFIX = re.compile(r"^\s*\d+[.)、]\s*")
FENCE_OPEN = re.compile(r"^\s*```")
FENCE_CLOSE = re.compile(r"^\s*```\s*$")
FRONT = re.compile(r"^\s*---\s*$")
GLOBISH = re.compile(r"[*?]|^\S+\.\w+$")
MODEL_VERB = re.compile(r"(撰写|总结|判断|评估|权衡|解释|建议|询问|确认|命名|设计|取舍)")
BAN_VERB = re.compile(r"(不要|不得|禁止|勿|不可)")
HEADING = re.compile(r"^#{1,6}\s")
# Sentences describing the model's job ("这部分需要判断") are not prohibitions.
MODEL_TALK = re.compile(r"(需要判断|由模型|不要写死|根据结果|解释风险|给出建议)")
INCLUDE_GLOB = re.compile(r"--include\s*=\s*[\"']?([^\"'\s]+)[\"']?")
# A script invocation in prose, e.g. "run scripts/parse.py" or "python3 foo.py".
SCRIPT_CALL = re.compile(
    r"(?:\bpython(?:3)?\s+|运行\s+|执行\s+|调用\s+)[`'\"]?([\w./-]+\.py)\b", re.I)

GENERATOR = "skill-forge propose %s" % VERSION


def _clean(line: str) -> str:
    return NUM_PREFIX.sub("", line.strip()).strip().rstrip("。;；")


def _tokens(line: str):
    return INLINE.findall(line)


def _glob(line: str, fallback: str):
    for tok in _tokens(line):
        if "*" in tok or "?" in tok:
            return tok
    m = INCLUDE_GLOB.search(line)
    if m:
        return "**/" + m.group(1)
    return fallback


def _source_range(lines, i: int) -> str:
    """Extend L<i> over an immediately following fenced shell block."""
    k = i + 1
    while k <= len(lines) and not lines[k - 1].strip():
        k += 1
    if k <= len(lines) and FENCE_OPEN.match(lines[k - 1]):
        m = k + 1
        while m <= len(lines) and not FENCE_CLOSE.match(lines[m - 1]):
            m += 1
        if m <= len(lines):
            return "SKILL.md:L%d-L%d" % (i, m)
    return "SKILL.md:L%d" % i


def _json_key(skill_dir: Path, rel: str):
    """Best-effort: pick the first scalar key of a referenced JSON config."""
    try:
        p = (skill_dir / rel).resolve()
        p.relative_to(skill_dir.resolve())
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, (str, int, float, bool)) and re.match(r"^[A-Za-z_]\w*$", k):
                    return k
    except Exception:
        return None
    return None


def _emit(base: str, used: set) -> str:
    name = re.sub(r"\W+", "_", base.lower()).strip("_") or "value"
    if not re.match(r"^[A-Za-z_]", name):
        name = "v_" + name
    out, n = name, 2
    while out in used:
        out = "%s_%d" % (name, n)
        n += 1
    used.add(out)
    return out


def _step(sid, source, owner, matched, reason, confidence, *, op=None,
          args=None, emit=None, note=None):
    s = {
        "id": sid,
        "source": source,
        "owner": owner,
        "proposal": {
            "confidence": confidence,
            "reason": reason,
            "matched_text": matched[:160],
        },
    }
    if op:
        s["op"] = op
        s["args"] = args or {}
        s["emit"] = emit
    if note:
        s["note"] = note
    return s


def _from_shell(line: str, glob: str):
    """Conservative mapping for an INDEPENDENT fenced shell block.

    Returns (op, args, emit_base, reason) or None. Always low confidence: a
    command in a SKILL.md is often a documentation example, not a fixed step.
    """
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if re.search(r"\bwc\s+-l\b", s):
        if re.search(r"\bgrep\b", s):
            m = re.search(r"grep[^'\"\n]*['\"]([^'\"]+)['\"]", s)
            if not m:
                return None
            pat = m.group(1)
            return ("count_matches",
                    {"glob": _glob(s, glob), "pattern": re.escape(pat), "mode": "lines"},
                    re.sub(r"\W+", "_", pat.lower()) + "_hits",
                    "独立 bash 块的 grep | wc -l → count_matches(lines)；须确认属固定流程而非文档示例")
        return ("count_lines", {"glob": _glob(s, glob)}, "line_count",
                "独立 bash 块的 wc -l → count_lines；须确认属固定流程而非文档示例")
    if re.search(r"\bsha256sum\b", s):
        return ("hash_files", {"glob": _glob(s, glob)}, "digest",
                "独立 bash 块的 sha256sum → hash_files；须确认属固定流程")
    m = re.search(r"(?:cat|jq)\s+([\w./-]+\.json)", s)
    if m:
        return ("read_json", {"path": m.group(1)}, "config",
                "独立 bash 块读取 JSON → read_json；须确认属固定流程")
    m = re.search(r"(?:cat|head|tail)\s+([\w./-]+\.csv)", s)
    if m:
        return ("parse_csv", {"path": m.group(1)}, "rows",
                "独立 bash 块读取 CSV → parse_csv；须确认属固定流程")
    m = re.search(r"(?:cat|head|tail)\s+([\w./-]+\.(?:txt|md|log|yaml|yml))", s)
    if m:
        return ("read_text", {"path": m.group(1)}, "text",
                "独立 bash 块读取文本 → read_text；须确认属固定流程")
    m = re.search(r"(?:test|\[)\s+-f\s+([\w./-]+)", s)
    if m:
        return ("file_exists", {"path": m.group(1)}, "exists",
                "独立 bash 块判断文件存在 → file_exists；须确认属固定流程")
    return None


def propose(skill_dir: Path, text: str, cands=None) -> dict:
    """Return a PROPOSED IR dict. Never writes files."""
    lines = text.splitlines()
    fm_end = analyzer.frontmatter_end(lines)
    code_lines = {c["line"] for c in (cands or []) if c["owner"] == "code"}
    steps, unmapped, kept, bans = [], [], [], []
    used, numeric = set(), []
    last_glob = "**/*"
    model_lines = []
    suggestions = []
    covered_end = 0
    skip_until = 0

    for i, raw in enumerate(lines, 1):
        if i <= skip_until or i <= fm_end:
            continue
        line = raw.strip()
        if FENCE_OPEN.match(line) and not FENCE_CLOSE.match(line):
            j = i + 1
            while j <= len(lines) and not FENCE_CLOSE.match(lines[j - 1].strip()):
                j += 1
            end = min(j, len(lines))
            skip_until = end
            if i <= covered_end:
                continue  # already claimed by the preceding prose step
            for k in range(i + 1, end + 1):
                m = INCLUDE_GLOB.search(lines[k - 1])
                if m:
                    last_glob = "**/" + m.group(1)
            mapped = 0
            for k in range(i + 1, end):
                spec = _from_shell(lines[k - 1], last_glob)
                if spec is None:
                    # A python <script.py> call maps to the L3 run_script op.
                    # NEVER auto-enable it: emit a suggestion for the human or
                    # an Agent-authored `forge fill` patch instead.
                    mm = SCRIPT_CALL.search(lines[k - 1])
                    if mm:
                        suggestions.append({
                            "line": k,
                            "text": lines[k - 1].strip()[:160],
                            "suggested_op": "run_script",
                            "requires": "L3：人审用 `forge approve --enable-run-script` 放通（补丁不得自我提权）",
                            "patch_hint": {
                                "steps_add": [{
                                    "id": "agent_run",
                                    "source": "SKILL.md:L%d" % k,
                                    "owner": "code", "op": "run_script",
                                    "args": {"path": mm.group(1), "timeout": 30},
                                    "emit": "script_result",
                                }],
                            },
                            "approve_hint": "forge approve <dir> --yes --by <姓名> --enable-run-script",
                        })
                    continue
                if mapped >= 2:
                    continue
                op, args, emit_base, reason = spec
                emit = _emit(emit_base, used)
                if op in ("count_files", "count_matches", "count_lines"):
                    numeric.append(emit)
                steps.append(_step(
                    "s%d" % (len(steps) + 1), "SKILL.md:L%d-L%d" % (i, end), "code",
                    lines[k - 1].strip()[:120], reason, "low",
                    op=op, args=args, emit=emit, note="从独立 bash 块推断（低置信）",
                ))
                mapped += 1
            continue
        if FENCE_CLOSE.match(line) or not line:
            continue
        clean = _clean(line)
        # boilerplate output-format lines are neither steps nor prohibitions
        if re.match(r"^(用中文|输出格式|以中文|注意[:：]|说明[:：])", clean):
            continue
        src = _source_range(lines, i)
        _rm = re.search(r"L(\d+)(?:-L(\d+))?$", src)
        if _rm:
            covered_end = max(covered_end, int(_rm.group(2) or _rm.group(1)))
        sid = "s%d" % (len(steps) + 1)

        # 1) prohibitions: keep the original wording, never paraphrase
        if BAN_VERB.search(clean) and not HEADING.match(clean) \
                and not MODEL_TALK.search(clean) \
                and not re.search(r"(统计|收集|读取|超过)", clean):
            for sentence in re.split(r"[。；;]", clean):
                if not BAN_VERB.search(sentence) or HEADING.match(sentence.strip()):
                    continue
                for part in re.split(r"[，,]\s*(?=也不要)", sentence):
                    part = part.strip().lstrip("也").strip()
                    if part and not HEADING.match(part) and part not in bans:
                        bans.append(part)

        # 2) model-owned judgment
        if MODEL_VERB.search(clean) and not re.search(r"(统计|读取|超过\s*\d)", clean):
            model_lines.append((i, clean))
            if clean not in kept:
                kept.append(clean)
            continue

        # 3) fixed threshold decision table
        m = re.search(
            r"超过\s*(\d+)\s*(?:→|->|则|时)\s*([A-Za-z_][A-Za-z0-9_]*)\s*[，,]?\s*否则\s*([A-Za-z_][A-Za-z0-9_]*)",
            clean,
        )
        if m and numeric:
            threshold, hit, fallback = m.groups()
            var = numeric[-1]
            steps.append(_step(
                sid, src, "code", clean,
                "固定决策表：'%s > %s' → %s，否则 %s；变量沿用最近的数值输出 %s" % (
                    var, threshold, hit, fallback, var),
                "high", op="decide",
                args={"rules": [{"when": "%s > %s" % (var, threshold), "status": hit}],
                      "else": fallback},
                emit="status", note="固定决策表",
            ))
            used.add("status")
            continue

        # 4) count files
        if re.search(r"统计", clean) and re.search(r"(总数|数量|个数|数目)", clean) \
                and not re.search(r"(含|包含|命中)", clean):
            last_glob = _glob(line, last_glob)
            ext = re.search(r"\*\.(\w+)", last_glob)
            emit = _emit((ext.group(1) if ext else "file") + "_count", used)
            numeric.append(emit)
            steps.append(_step(
                sid, src, "code", clean,
                "统计动词 + 文件模式：映射为只读 count_files(%s)" % last_glob,
                "high", op="count_files", args={"glob": last_glob}, emit=emit,
                note="统计数量",
            ))
            continue

        # 5) count matches
        if re.search(r"统计含", clean) or (re.search(r"统计", clean) and re.search(r"标记|包含", clean)):
            pat = next((t for t in _tokens(line) if not GLOBISH.search(t) and len(t) < 40), None)
            if pat:
                mode = "lines" if re.search(r"行数|命中行", clean) else "files"
                emit = _emit(re.sub(r"\W+", "_", pat.lower()) + "_hits", used)
                numeric.append(emit)
                args = {"glob": last_glob, "pattern": re.escape(pat), "mode": mode}
                steps.append(_step(
                    sid, src, "code", clean,
                    "统计含 `%s` 标记的条目：映射为 count_matches，pattern 取自原文反引号；沿用上一处 glob %s" % (
                        pat, last_glob),
                    "medium", op="count_matches", args=args, emit=emit,
                    note="统计命中数",
                ))
                continue

        # 6) collect list
        if re.search(r"(收集|列出)", clean) and re.search(r"(列表|清单|文件名)", clean):
            lim = re.search(r"最多\s*(\d+)", clean)
            base = "watch_list" if re.search(r"(关注|监控|观察|待办)", clean) else "file_list"
            emit = _emit(base, used)
            args = {"glob": last_glob, "limit": int(lim.group(1)) if lim else 20}
            steps.append(_step(
                sid, src, "code", clean,
                "收集类动作：映射为 collect，limit 取自原文「最多 N 个」（缺省 20）",
                "medium", op="collect", args=args, emit=emit, note="收集文件列表",
            ))
            continue

        # 7) read json config (+ best-effort json_get)
        m = re.search(r"读取\s*[`]?([\w./-]+\.json)[`]?", clean)
        if m:
            rel = m.group(1)
            emit = _emit("config", used)
            steps.append(_step(
                sid, src, "code", clean,
                "读取 JSON 配置：映射为只读 read_json(%s)" % rel,
                "high", op="read_json", args={"path": rel}, emit=emit, note="读取配置",
            ))
            key = _json_key(skill_dir, rel)
            if key:
                kem = _emit(key, used)
                steps.append(_step(
                    "s%d" % (len(steps) + 1), src, "code", clean,
                    "同句含「取得/配置」：从 %s 的实际键推断出 %s；请人审该键是否真为所需字段" % (rel, key),
                    "low", op="json_get",
                    args={"from": emit, "key": key, "default": "unknown"},
                    emit=kem, note="取得配置字段",
                ))
            else:
                unmapped.append({
                    "line": i, "text": clean,
                    "reason": "已生成 read_json，但无法从 %s 推断要取哪个字段；如需请手工补 json_get" % rel,
                })
            continue

        # 8) script invocation in prose -> L3 suggestion, never auto-enabled
        m = SCRIPT_CALL.search(clean)
        if m:
            suggestions.append({
                "line": i,
                "text": clean[:160],
                "suggested_op": "run_script",
                "requires": "L3：人审用 `forge approve --enable-run-script` 放通（补丁不得自我提权）",
                "patch_hint": {
                    "steps_add": [{
                        "id": "agent_run",
                        "source": "SKILL.md:L%d" % i,
                        "owner": "code", "op": "run_script",
                        "args": {"path": m.group(1), "timeout": 30},
                        "emit": "script_result",
                    }],
                },
                "approve_hint": "forge approve <dir> --yes --by <姓名> --enable-run-script",
            })

        if i in code_lines:
            unmapped.append({
                "line": i, "text": clean,
                "reason": "静态扫描判为确定性步骤，但启发式无法安全映射为内置 op（宁缺毋滥）",
            })

    # merge contiguous model lines into one model step
    if model_lines:
        first, last = model_lines[0][0], model_lines[-1][0]
        src = "SKILL.md:L%d" % first if first == last else "SKILL.md:L%d-L%d" % (first, last)
        steps.append(_step(
            "s%d" % (len(steps) + 1), src, "model",
            " / ".join(t for _, t in model_lines),
            "含判断/撰写/确认动词，结果不由输入唯一决定 → 保留给模型",
            "high", note="；".join(t for _, t in model_lines),
        ))

    if not kept:
        kept = ["根据结构化结果撰写结论"]
    if not bans:
        bans = ["全程只读，不修改任何文件"]

    warnings = []
    if unmapped:
        warnings.append("有 %d 行未能自动映射，已列入 proposal.unmapped，人审时手工补充" % len(unmapped))
    if any(s.get("proposal", {}).get("confidence") == "low" for s in steps):
        warnings.append("存在 low-confidence 步骤（配置字段键为启发式推断），必须逐条核对")
    if not any(s.get("op") == "decide" for s in steps):
        warnings.append("没有推断出 decide 步骤，STATUS 将恒为 OK；如原文有阈值表请手工补")
    if suggestions:
        warnings.append("发现 %d 处 python 脚本调用，需要 L3 的 run_script op："
                        "已列入 proposal.suggestions，未自动放通；用 `forge fill` 显式补写" % len(suggestions))

    counts = {k: sum(1 for c in (cands or []) if c["owner"] == k)
              for k in ("code", "model", "review")}
    code_steps = [s for s in steps if s.get("owner") == "code"]

    # Declared plugins are *capabilities*, not a grant. propose records the
    # whitelist and hash so `fill` can add plugin_* steps; the L3 switch stays
    # false and only `approve --enable-plugins` opens it.
    declared_plugins, plugin_errors = oppack.discover(skill_dir)
    if plugin_errors:
        warnings.extend(plugin_errors)
        warnings.append("plugins/ 存在错误，本次草案不声明任何插件")
        declared_plugins = []
    if declared_plugins:
        warnings.append("发现 %d 个已声明插件（%s）：草案只登记白名单与哈希，"
                        "未开启 allow_plugins；用 `forge fill` 补步骤后由人审放通" % (
                            len(declared_plugins),
                            ", ".join(p["name"] for p in declared_plugins)))
    draft = {
        "version": "1",
        "target": slugify_target(skill_dir.name),
        "source": "SKILL.md",
        "source_sha256": sha256_text(text),
        "allow_shell": False,
        "allow_run_script": False,
        "allow_plugins": False,
        "review_status": "PROPOSED",
        "gates": ["read_only", "no_network"],
        "prohibitions": bans,
        "kept_for_model": kept,
        "steps": steps,
        "proposal": {
            "generator": GENERATOR,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "candidate_counts": counts,
            "inferred_code_steps": len(code_steps),
            "mapped_lines": [s["source"] for s in steps],
            "unmapped": unmapped,
            "suggestions": suggestions,
            "warnings": warnings,
            "policy": "本草案不可直接编译。必须执行 forge approve <dir> --yes 显式人审后才能 compile。",
        },
    }
    if declared_plugins:
        draft["plugin_ops"] = [p["name"] for p in declared_plugins]
        draft["plugins_sha256"] = oppack.digest(declared_plugins)
    return draft


def approve(data: dict, reviewer: str = "human") -> dict:
    """Strip proposal metadata and mark the IR as human-approved."""
    out = {k: v for k, v in data.items() if k != "proposal"}
    out["review_status"] = "APPROVED"
    out["approved_at"] = datetime.now().isoformat(timespec="seconds")
    out["approved_by"] = reviewer
    out["steps"] = [
        {k: v for k, v in s.items() if k != "proposal"} if isinstance(s, dict) else s
        for s in out.get("steps", [])
    ]
    return out


def review_sheet(data: dict) -> str:
    """Human-readable checklist printed by `forge approve`."""
    rows = ["| # | 来源 | 归属 | op | 置信 | 推断依据 |", "|---|---|---|---|---|---|"]
    for i, s in enumerate(data.get("steps", []), 1):
        p = s.get("proposal", {})
        rows.append("| %d | %s | %s | %s | %s | %s |" % (
            i, s.get("source", ""), s.get("owner", ""), s.get("op", "—"),
            p.get("confidence", "—"), str(p.get("reason", "")).replace("|", "\\|")[:110],
        ))
    lines = [
        "# IR 人审清单 · %s" % data.get("target", "?"), "",
        "生成器：%s" % data.get("proposal", {}).get("generator", "?"),
        "推断 code 步骤：%d" % data.get("proposal", {}).get("inferred_code_steps", 0), "",
        *rows, "", "## 保留给模型", "",
        *["- " + str(n) for n in data.get("kept_for_model", [])], "",
        "## 禁止动作", "",
        *["- " + str(n) for n in data.get("prohibitions", [])], "",
        "## 未自动映射（需手工补或确认放弃）", "",
    ]
    for u in data.get("proposal", {}).get("unmapped", []):
        lines.append("- L%d %s — %s" % (u["line"], u["text"], u["reason"]))
    if not data.get("proposal", {}).get("unmapped"):
        lines.append("（无）")
    lines += ["", "## L3 建议（未自动放通，需显式补丁）", ""]
    for s in data.get("proposal", {}).get("suggestions", []):
        lines.append("- L%d `%s` → `%s`（%s）" % (
            s.get("line", 0), str(s.get("text", "")).replace("|", "\\|")[:100],
            s.get("suggested_op", "?"), s.get("requires", "")))
    if not data.get("proposal", {}).get("suggestions"):
        lines.append("（无）")
    lines += ["", "## 插件声明（L3，默认不放通）", ""]
    if data.get("plugin_ops"):
        lines.append("- 白名单：%s" % ", ".join(data["plugin_ops"]))
        lines.append("- plugins/ 摘要：`%s`" % data.get("plugins_sha256", ""))
        lines.append("- 人审前请逐个打开 `plugins/*.py`；内容变更即视为漂移")
    else:
        lines.append("（无）")
    lines += ["", "## 警告", ""]
    for w in data.get("proposal", {}).get("warnings", []):
        lines.append("- " + w)
    if not data.get("proposal", {}).get("warnings"):
        lines.append("（无）")
    lines += ["", "确认以上每条 `source` 指回原文无误后，加 `--yes` 生成正式 IR。", ""]
    return "\n".join(lines)
