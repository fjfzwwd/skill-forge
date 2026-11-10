"""Emit a self-contained runner, a compressed SKILL.md, report and lockfile."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import oppack
from ir import sha256_file, sha256_text
from version import VERSION, GENERATED_BANNER

RUNTIME_LIB = '''#!/usr/bin/env python3
# __BANNER__
# Regenerate with: python forge.py compile <skill_dir> --ir <ir.json>
# Self-contained: standard library only. No network, no writes.
from pathlib import Path
import csv
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import time

DEFAULT_ROOT = "__DEFAULT_ROOT__"
ALLOW_SHELL = __ALLOW_SHELL__
ALLOW_RUN_SCRIPT = __ALLOW_RUN_SCRIPT__
ALLOW_PLUGINS = __ALLOW_PLUGINS__
MAX_SCRIPT_BYTES = 65536

# Windows consoles may default to a non-UTF-8 codepage. Force UTF-8 and never
# let an encoding error turn a valid run into a crash.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

_args = sys.argv[1:]
if "--root" in _args:
    _i = _args.index("--root")
    if _i + 1 >= len(_args):
        print("=== RESULT ===")
        print("STATUS=ERROR")
        print("ERROR=--root 缺少参数")
        print("=== ISSUES ===")
        print("ISSUE_COUNT=1")
        print("=== META ===")
        print("STEPS=0")
        sys.exit(2)
    ROOT = Path(_args[_i + 1]).resolve()
else:
    ROOT = (Path(__file__).resolve().parent / DEFAULT_ROOT).resolve()

# Model-owned work (kept in SKILL.md, never compiled):
__MODEL_NOTES__


def _safe_base(sub):
    base = (ROOT / sub).resolve()
    try:
        base.relative_to(ROOT)
    except ValueError:
        raise ValueError("path escapes skill root: " + str(sub))
    return base


def _iter_files(glob, root="."):
    base = _safe_base(root)
    out = []
    try:
        matches = sorted(base.glob(glob))
    except Exception as exc:
        raise ValueError("invalid glob %s (%s)" % (glob, exc))
    for p in matches:
        if not p.is_file():
            continue
        try:
            p.resolve().relative_to(base)
        except ValueError:
            continue
        out.append(p)
    return out


def _read_bytes(path, max_bytes=65536):
    base = _safe_base(".")
    p = (base / path).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        raise ValueError("path escapes skill root: " + str(path))
    if not p.is_file():
        raise ValueError("file not found: " + str(path))
    data = p.read_bytes()[:max_bytes]
    # Excel/Windows CSVs commonly start with a UTF-8 BOM. Decoding with plain
    # utf-8 leaves U+FEFF glued to the first column name, which silently
    # breaks parse_csv -> pluck and any header check. utf-8-sig strips it.
    return data.decode("utf-8-sig", errors="replace")


def op_count_files(glob, root="."):
    return len(_iter_files(glob, root))


def op_count_matches(glob, pattern, root=".", mode="files"):
    rx = re.compile(pattern)
    total = 0
    for p in _iter_files(glob, root):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        hits = sum(1 for ln in txt.splitlines() if rx.search(ln))
        if hits:
            total += hits if mode == "lines" else 1
    return total


def op_count_lines(glob, root="."):
    total = 0
    for p in _iter_files(glob, root):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        total += len(txt.splitlines())
    return total


def op_collect(glob, root=".", limit=50):
    base = _safe_base(root)
    return [p.relative_to(base).as_posix() for p in _iter_files(glob, root)[:int(limit)]]


def op_file_exists(path):
    base = _safe_base(".")
    p = (base / path).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        return False
    return p.is_file()


def op_read_text(path, max_bytes=65536):
    return _read_bytes(path, int(max_bytes))


def op_read_json(path):
    return json.loads(_read_bytes(path, MAX_SCRIPT_BYTES))


def op_parse_csv(path, limit=100):
    txt = _read_bytes(path, MAX_SCRIPT_BYTES)
    return [dict(r) for r in list(csv.DictReader(io.StringIO(txt)))[:int(limit)]]


def op_json_get(obj, key, default=None):
    cur = obj
    for part in str(key).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def op_pluck(obj, key, limit=100):
    if not isinstance(obj, list):
        return []
    out = []
    for item in obj[:int(limit)]:
        if isinstance(item, dict) and key in item:
            out.append(item[key])
    return out


def op_regex_extract(obj, pattern, group=0, limit=20):
    rx = re.compile(pattern)
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    out = []
    for m in rx.finditer(text):
        try:
            out.append(m.group(int(group)))
        except (IndexError, ValueError):
            out.append(m.group(0))
        if len(out) >= int(limit):
            break
    return out


def op_hash_files(glob, root="."):
    h = hashlib.sha256()
    for p in _iter_files(glob, root):
        h.update(p.relative_to(_safe_base(root)).as_posix().encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def _literal(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    if re.fullmatch(r"-?\\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\\d+\\.\\d+", raw):
        return float(raw)
    raise ValueError("unsupported literal: " + raw)


def _cond(expr, ctx):
    m = re.match(r"^\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*(>=|<=|==|!=|>|<)\\s*(.+?)\\s*$", expr or "")
    if not m:
        raise ValueError("unsupported condition: " + str(expr))
    name, op, raw = m.groups()
    if name not in ctx:
        raise ValueError("undefined variable: " + name)
    left, right = ctx[name], _literal(raw)
    if isinstance(left, (list, dict)):
        left = len(left)
    try:
        if op == ">":
            return left > right
        if op == "<":
            return left < right
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
    except TypeError as exc:
        raise ValueError("condition type mismatch: %s (%s)" % (expr, exc))
    raise ValueError("unsupported operator: " + op)


def op_decide(ctx, rules, fallback="OK"):
    for r in rules:
        if _cond(r["when"], ctx):
            return r["status"]
    return fallback


def op_shell(cmd, timeout=30):
    if not ALLOW_SHELL:
        raise ValueError("shell op disabled by policy")
    proc = subprocess.run(shlex.split(cmd), cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=int(timeout), shell=False)
    if proc.returncode != 0:
        raise ValueError("shell rc=%d: %s" % (proc.returncode, proc.stderr.strip()[:200]))
    return proc.stdout.strip()[:MAX_SCRIPT_BYTES]


def op_run_script(path, argv=None, timeout=30, max_bytes=65536):
    """Run a .py script INSIDE the skill directory. Never uses a shell.

    This is the L3 escape hatch: it executes arbitrary Python owned by the
    skill. It requires allow_run_script=true and a human-signed IR.
    """
    if not ALLOW_RUN_SCRIPT:
        raise ValueError("run_script op disabled by policy")
    if not str(path).lower().endswith(".py"):
        raise ValueError("run_script only accepts .py files")
    base = _safe_base(".")
    p = (base / path).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        raise ValueError("run_script path escapes skill root: " + str(path))
    if not p.is_file():
        raise ValueError("run_script file not found: " + str(path))
    argv = [str(a) for a in (argv or [])]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, str(p)] + argv, cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=int(timeout), shell=False,
                          env=env)
    if proc.returncode != 0:
        raise ValueError("run_script rc=%d: %s" % (
            proc.returncode, proc.stderr.strip()[:300]))
    out = proc.stdout.strip()[:int(max_bytes)]
    try:
        return json.loads(out)
    except (ValueError, TypeError):
        return out


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        s = ",".join(_fmt(x) for x in v)
    elif isinstance(v, dict):
        s = json.dumps(v, ensure_ascii=False, sort_keys=True)
    else:
        s = str(v)
    s = re.sub(r"[\\x00-\\x1f]+", " ", s)
    return s[:400]


__PLUGIN_BLOCK__


def main():
    t0 = time.time()
    ctx = {}
    steps = 0
__STEPS__
    print("=== RESULT ===")
    print("STATUS=" + _fmt(ctx.get("status", "OK")))
    for k, v in ctx.items():
        if k != "status":
            print(k.upper() + "=" + _fmt(v))
    print("=== ISSUES ===")
    print("ISSUE_COUNT=" + _fmt(ctx.get("issue_count", 0)))
    print("=== META ===")
    print("ELAPSED_MS=" + str(int((time.time() - t0) * 1000)))
    print("STEPS=" + str(steps))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("=== RESULT ===")
        print("STATUS=ERROR")
        print("ERROR=" + _fmt(str(exc)))
        print("=== ISSUES ===")
        print("ISSUE_COUNT=1")
        print("=== META ===")
        print("ELAPSED_MS=0")
        print("STEPS=0")
        sys.exit(1)
'''


def _py(value) -> str:
    """Render a *Python* literal for generated code.

    The runner is Python source, not JSON: json.dumps(False) emits `false`,
    json.dumps(None) emits `null`, both of which are NameErrors at runtime.
    Using repr() keeps bool/None/str/list/dict valid Python literals.
    """
    return repr(value)


def _step_code(step: dict) -> str:
    args = step.get("args", {})
    op, emit = step["op"], step["emit"]
    if op.startswith(oppack.PREFIX):
        call = "op_plugin(ctx, %s%s)" % (
            _py(op[len(oppack.PREFIX):]),
            ("".join(", %s=%s" % (k, _py(v)) for k, v in args.items())))
    elif op == "decide":
        call = "op_decide(ctx, %s, %s)" % (
            _py(args["rules"]), _py(args.get("else", "OK")))
    elif op in ("json_get", "regex_extract", "pluck"):
        ref = _py(args["from"])
        if op == "json_get":
            call = "op_json_get(ctx[%s], %s, %s)" % (
                ref, _py(args["key"]), _py(args.get("default")))
        elif op == "pluck":
            pairs = ", ".join("%s=%s" % (k, _py(v))
                              for k, v in args.items() if k != "from")
            call = "op_pluck(ctx[%s]%s)" % (ref, (", " + pairs) if pairs else "")
        else:
            pairs = ", ".join("%s=%s" % (k, _py(v))
                              for k, v in args.items() if k != "from")
            call = "op_regex_extract(ctx[%s]%s)" % (ref, (", " + pairs) if pairs else "")
    else:
        pairs = ", ".join("%s=%s" % (k, _py(v)) for k, v in args.items())
        call = "op_%s(%s)" % (op, pairs)
    return "    # %s <- %s\n    ctx[%s] = %s\n    steps += 1" % (
        step["id"], step["source"], _py(emit), call,
    )


def render_runner(ir: dict, data_root_rel: str = "../..", plugins=None) -> str:
    notes = ir.get("kept_for_model", []) or ["(none declared)"]
    notes_block = "\n".join("#   - " + str(n).replace("\n", " ") for n in notes)
    steps = [s for s in ir["steps"] if s.get("owner") == "code"]
    body = RUNTIME_LIB
    body = body.replace("__BANNER__", GENERATED_BANNER)
    body = body.replace("__DEFAULT_ROOT__", data_root_rel)
    body = body.replace("__ALLOW_SHELL__", "True" if ir.get("allow_shell") else "False")
    body = body.replace("__ALLOW_RUN_SCRIPT__", "True" if ir.get("allow_run_script") else "False")
    body = body.replace("__ALLOW_PLUGINS__", "True" if ir.get("allow_plugins") else "False")
    body = body.replace("__PLUGIN_BLOCK__", oppack.plugin_block(plugins or []))
    body = body.replace("__MODEL_NOTES__", notes_block)
    body = body.replace("__STEPS__", "\n".join(_step_code(s) for s in steps) or "    pass")
    return body


def _frontmatter(text: str) -> dict:
    """Extract frontmatter key/value pairs without a YAML dependency."""
    m = re.match(r"^\ufeff?---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    out = {}
    if not m:
        return out
    for line in m.group(1).splitlines():
        mm = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if mm and mm.group(2).strip() not in (">", "|", ">-", "|-"):
            out[mm.group(1)] = mm.group(2).strip()
    return out


def render_minimal_skill(ir: dict, source_text: str) -> str:
    """Emit an installable SKILL.md: frontmatter is always present.

    The original description is deliberately NOT copied verbatim: it describes
    the old prose procedure and would defeat the point of compaction. A short
    generated description is used, overridable with `description` in the IR.
    """
    statuses = []
    for s in ir["steps"]:
        if s.get("op") == "decide":
            statuses = [r["status"] for r in s["args"]["rules"]]
            if s["args"].get("else"):
                statuses.append(s["args"]["else"])
    fm = _frontmatter(source_text)
    name = fm.get("name") or ir["target"]
    desc = ir.get("description") or (
        "%s 的结晶版：确定性步骤由 scripts/crystallized.py 执行，模型只负责按结构化结果做判断。"
        % ir["target"])
    license_ = fm.get("license", "MIT")
    head = "\n".join([
        "---",
        "name: %s" % name,
        "description: %s" % desc.replace("\n", " ").strip(),
        "license: %s" % license_,
        "metadata:",
        "  version: %s" % VERSION,
        "  generated_by: skill-forge",
        "  risk: %s" % ("L3" if (ir.get("allow_run_script") or ir.get("allow_plugins"))
                        else ("L2" if ir.get("allow_shell") else "L1")),
        "  requires: python>=3.9",
        "---", "",
    ])
    lines = [
        "# %s" % ir["target"], "",
        "运行 `python scripts/crystallized.py`，读取 `=== RESULT ===` 段的结构化输出。",
        "", "按 STATUS 决策：",
    ]
    for st in statuses or ["OK"]:
        lines.append("- `%s` → 按下方模型职责完成结论" % st)
    lines += ["", "模型职责（不要写成脚本、不要重写确定性步骤）："]
    for n in ir.get("kept_for_model", []) or ["按结构化结果撰写结论"]:
        lines.append("- " + str(n))
    if ir.get("prohibitions"):
        lines += ["", "禁止："] + ["- " + str(p) for p in ir["prohibitions"]]
    lines += [
        "", "STATUS=ERROR 时不要猜测，直接报告 ERROR 字段。",
        "源文档变更后须重新编译，不要手改生成的脚本。",
    ]
    return head + "\n".join(lines) + "\n"


def render_report(ir: dict, source_text: str, minimal: str, cands=None) -> str:
    code_steps = [s for s in ir["steps"] if s.get("owner") == "code"]
    model_steps = [s for s in ir["steps"] if s.get("owner") == "model"]
    orig = len(source_text.splitlines())
    new = len(minimal.splitlines())
    rows = ["| id | 来源 | op | 输出 |", "|---|---|---|---|"]
    for s in code_steps:
        rows.append("| %s | %s | %s | `%s` |" % (s["id"], s["source"], s["op"], s["emit"]))
    privileged = [s["op"] for s in code_steps
                  if s["op"] in ("shell", "run_script")
                  or s["op"].startswith(oppack.PREFIX)]
    return "\n".join([
        "# 结晶报告 · %s" % ir["target"], "",
        "生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "",
        "生成器：skill-forge %s" % VERSION, "",
        "## 概览", "",
        "| 指标 | 值 |", "|---|---|",
        "| 源 SKILL.md 行数 | %d |" % orig,
        "| 精简版行数 | %d |" % new,
        "| 行数压缩 | %.0f%% |" % (100 * (1 - new / max(orig, 1))),
        "| 可结晶步骤 | %d |" % len(code_steps),
        "| 保留给模型 | %d |" % len(model_steps),
        "| 原工具往返 | 约 %d 次 |" % len(code_steps),
        "| 编译后工具往返 | 1 次 |",
        "| 风险等级 | %s |" % (
            "L3（含 run_script / plugin）" if (
                "run_script" in privileged or ir.get("allow_plugins"))
            else ("L2（含 shell）" if "shell" in privileged else "L1（只读）")),
        "", "## 已结晶步骤", "", *rows, "",
        "## 保留给模型", "",
        *["- %s" % n for n in ir.get("kept_for_model", [])], "",
        "## 收益估算（非实测）", "",
        "- 上下文：源文档约 %d 行 → 精简版约 %d 行，按 12 token/行粗估，每次调用省约 %d token。" % (
            orig, new, max(0, (orig - new) * 12)),
        "- 执行：%d 次命令 + %d 份原始输出 → 1 次脚本调用 + 1 份 `KEY=VALUE` 结果。" % (
            len(code_steps), len(code_steps)),
        "- 稳定性：确定性步骤由代码执行，消除模型跳步/乱序/重复解释带来的波动。",
        "- **以上为估算**。实测用 `forge measure <dir> --runs N`；`forge report <dir>` 会把实测数据合并成 "
        "`build/report.measured.md`（该文件不进漂移锁）。真实 token / 遵循率仍需外部 eval runner。", "",
        "## 人审清单（编译后、替换前）", "",
        "1. 每个 `source` 指回的原文确实是该动作。",
        "2. `kept_for_model` 未漏掉判断性步骤。",
        "3. 禁止动作已完整搬到精简版。",
        "4. `allow_shell` / `allow_run_script` / `allow_plugins` 保持 `false`，除非逐条审过。",
        "5. `forge verify` 基线已更新并通过。", "",
        "产物在 `build/`，**不会覆盖源 SKILL.md**。确认后再手工替换。",
    ]) + "\n"


def render_measured_report(ir: dict, source_text: str, minimal: str,
                           measurement: dict) -> str:
    """Estimate report + real measurements, written OUTSIDE the drift lock.

    `report.md` is one of LOCKED_ARTIFACTS, so it must stay deterministic and
    must never be rewritten after compilation. Measured data therefore lives
    in a separate file that `watch` deliberately ignores.
    """
    base = render_report(ir, source_text, minimal).rstrip()
    p = measurement
    rows = [
        "| 指标 | 实测值 |", "|---|---|",
        "| 测量时间 | %s |" % p.get("measured_at", "?"),
        "| 请求运行次数 | %s |" % p.get("requested_runs", "?"),
        "| 成功次数 | %s |" % p.get("successful_runs", "?"),
        "| 输出形状稳定 | %s |" % ("是" if p.get("shape_stable") else "否"),
        "| 墙钟时间中位数 | %s ms |" % p.get("elapsed_ms_median", "?"),
        "| 墙钟时间 P95 | %s ms |" % p.get("elapsed_ms_p95", "?"),
        "| 输出字节中位数 | %s B |" % p.get("output_bytes_median", "?"),
    ]
    return "\n".join([
        base, "",
        "---", "",
        "## 实测数据（来自 `build/measurement.json`）", "",
        *rows, "",
        "逐次明细见 `build/measurement.md`。", "",
        "**口径**：%s" % p.get("scope_note", "实测范围见 measurement.json"), "",
        "本报告由 `forge measure` / `forge report` 生成，不属于编译产物，"
        "因此不会触发 `forge watch` 漂移。", "",
    ]) + "\n"


def render_lock(ir: dict, source_sha: str, ir_sha: str, files: dict) -> str:
    return json.dumps({
        "tool": "skill-forge",
        "version": VERSION,
        "target": ir["target"],
        "compiled_at": datetime.now().isoformat(timespec="seconds"),
        "source_sha256": source_sha,
        "ir_sha256": ir_sha,
        "allow_shell": bool(ir.get("allow_shell")),
        "allow_run_script": bool(ir.get("allow_run_script")),
        "allow_plugins": bool(ir.get("allow_plugins")),
        "plugins_sha256": ir.get("plugins_sha256", ""),
        "approved_by": ir.get("approved_by", ""),
        "files": files,
        "backup": "build/backup/SKILL.md",
    }, ensure_ascii=False, indent=2) + "\n"


LOCKED_ARTIFACTS = (
    "scripts/crystallized.py", "SKILL.md", "report.md", "ir.final.json",
)


def hash_tree(build: Path) -> dict:
    """Only compiled artifacts are locked. scan/plan/verify outputs are runtime data."""
    out = {}
    for rel in LOCKED_ARTIFACTS:
        p = build / rel
        if p.is_file():
            out[rel] = sha256_file(p)
    return out
