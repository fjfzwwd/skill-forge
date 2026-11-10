"""Crystallize IR v1.1: validation and restricted condition evaluation.

Zero third-party dependencies. Python 3.9+.

Design invariants:
  * IR is the object a human reviews. It must be readable, diffable, signable.
  * Every code step points back to a real line range in the source SKILL.md.
  * Nothing here calls eval(), compiles arbitrary expressions, or touches the
    network. Conditions use a restricted comparator grammar only.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

# op -> allowed argument keys. Anything else is rejected at compile time.
OPS = {
    "count_files": {"glob", "root"},
    "count_matches": {"glob", "pattern", "root", "mode"},
    "collect": {"glob", "root", "limit"},
    "read_json": {"path"},
    "json_get": {"from", "key", "default"},
    "hash_files": {"glob", "root"},
    "decide": {"rules", "else"},
    "shell": {"cmd", "timeout"},
    # --- v1.1 additions -------------------------------------------------
    "run_script": {"path", "argv", "timeout", "max_bytes"},
    "read_text": {"path", "max_bytes"},
    "parse_csv": {"path", "limit"},
    "count_lines": {"glob", "root"},
    "file_exists": {"path"},
    "regex_extract": {"from", "pattern", "group", "limit"},
    "pluck": {"from", "key", "limit"},
}

# Ops that reference a previously emitted variable via args["from"].
REF_OPS = {"json_get", "regex_extract", "pluck"}
# Ops that need an explicit top-level opt-in switch.
PRIVILEGED_OPS = {"shell": "allow_shell", "run_script": "allow_run_script"}
PLUGIN_PREFIX = "plugin_"
PLUGIN_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TARGET_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
STATUS_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SOURCE = re.compile(r"^SKILL\.md:L(\d+)(?:-L(\d+))?$")
# Upper-cased emit names must not collide with the runner's protocol keys.
RESERVED_EMITS = {
    "status", "issue_count", "elapsed_ms", "steps", "error",
    "result", "issues", "meta",
}
COND = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|==|!=|>|<)\s*(.+?)\s*$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
WIN_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
BAD_CHARS = re.compile(r"[\x00-\x1f]")


def slugify_target(name: str) -> str:
    """Turn a real directory name into a legal IR target.

    Skill directories routinely contain dots, spaces or CJK characters
    (e.g. `my.skill`, `tmp.XyZ`). Feeding that name straight into `target`
    used to make propose -> approve -> compile fail at the first validate.
    """
    out = re.sub(r"[^A-Za-z0-9_-]", "_", str(name or ""))
    if not out or not re.match(r"^[A-Za-z_]", out):
        out = "skill_" + out
    return out[:64]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _literal(raw: str):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    raise ValueError("条件右侧只支持数字/布尔/带引号字符串: %s" % raw)


def eval_condition(expr: str, ctx: dict) -> bool:
    """Restricted comparator. Never uses eval()."""
    m = COND.match(expr or "")
    if not m:
        raise ValueError("不支持的条件表达式: %s" % expr)
    name, op, raw = m.groups()
    if name not in ctx:
        raise ValueError("条件引用了未定义变量: %s" % name)
    left = ctx[name]
    right = _literal(raw)
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
        raise ValueError("条件类型不匹配: %s" % expr) from exc
    raise ValueError("不支持的运算符: %s" % op)


def bad_path(value) -> bool:
    """Reject absolute paths (POSIX, Windows drive, UNC), traversal and NUL."""
    if not isinstance(value, str):
        return True
    if not value or BAD_CHARS.search(value):
        return True
    if WIN_ABS.match(value) or value.startswith(("/", "\\")):
        return True
    norm = value.replace("\\", "/")
    if ".." in norm.split("/"):
        return True
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
        return True
    return False


def _check_arg_types(tag: str, op: str, args: dict, errors: list) -> None:
    """Compile-time argument type/range validation.

    A bad IR must never produce a runner that only fails at execution time.
    """
    str_keys = {"glob", "root", "path", "pattern", "key", "cmd", "from"}
    for k in str_keys & set(args):
        if not isinstance(args[k], str):
            errors.append("%s.args.%s 必须是字符串" % (tag, k))
        elif k in ("root", "path", "glob") and bad_path(args[k]):
            errors.append("%s.args.%s 路径越界（禁止绝对路径、盘符、..、URL）" % (tag, k))
    for k in ("limit", "timeout", "max_bytes", "group"):
        if k in args:
            v = args[k]
            if not isinstance(v, int) or isinstance(v, bool):
                errors.append("%s.args.%s 必须是整数" % (tag, k))
            elif k == "limit" and not (1 <= v <= 10000):
                errors.append("%s.args.limit 必须在 1..10000" % tag)
            elif k == "timeout" and not (1 <= v <= 300):
                errors.append("%s.args.timeout 必须在 1..300 秒" % tag)
            elif k == "max_bytes" and not (1 <= v <= 2_000_000):
                errors.append("%s.args.max_bytes 必须在 1..2000000" % tag)
            elif k == "group" and v < 0:
                errors.append("%s.args.group 必须 ≥ 0" % tag)
    if "mode" in args and args["mode"] not in ("files", "lines"):
        errors.append("%s.args.mode 只能是 files 或 lines" % tag)
    if "pattern" in args and isinstance(args["pattern"], str):
        try:
            re.compile(args["pattern"])
        except re.error as exc:
            errors.append("%s.args.pattern 不是合法正则: %s" % (tag, exc))
    if "argv" in args:
        v = args["argv"]
        if not isinstance(v, list) or any(not isinstance(x, (str, int, float)) for x in v):
            errors.append("%s.args.argv 必须是字符串/数字数组" % tag)
        elif any(BAD_CHARS.search(str(x)) for x in v):
            errors.append("%s.args.argv 含控制字符" % tag)
    if op == "run_script":
        p = args.get("path")
        if isinstance(p, str) and not bad_path(p) and not p.lower().endswith(".py"):
            errors.append("%s run_script 只允许技能目录内的 .py 脚本" % tag)
        if "path" not in args:
            errors.append("%s run_script 缺少 args.path" % tag)
    if op == "shell" and "cmd" not in args:
        errors.append("%s shell 缺少 args.cmd" % tag)
    if op == "decide":
        rules = args.get("rules")
        if not isinstance(rules, list) or not rules:
            errors.append("%s.args.rules 必须是非空数组" % tag)
        else:
            for r in rules:
                if not isinstance(r, dict) or not r.get("when") or not r.get("status"):
                    errors.append("%s.args.rules 每项需含 when 与 status" % tag)
                    continue
                if not STATUS_IDENT.match(str(r["status"])):
                    errors.append("%s status 必须是合法标识符: %r" % (tag, r["status"]))
        if "else" in args and not STATUS_IDENT.match(str(args["else"])):
            errors.append("%s.args.else 必须是合法标识符" % tag)
    if op == "read_text" and "path" not in args:
        errors.append("%s read_text 缺少 args.path" % tag)
    if op == "parse_csv" and "path" not in args:
        errors.append("%s parse_csv 缺少 args.path" % tag)
    if op == "file_exists" and "path" not in args:
        errors.append("%s file_exists 缺少 args.path" % tag)
    if op in ("regex_extract", "pluck") and "from" not in args:
        errors.append("%s 缺少 args.from" % tag)
    if op == "regex_extract" and "pattern" not in args:
        errors.append("%s regex_extract 缺少 args.pattern" % tag)
    if op == "pluck" and "key" not in args:
        errors.append("%s pluck 缺少 args.key" % tag)


def validate(ir: dict, source_text: str, expect_target: str = ""):
    """Return (errors, warnings). Non-empty errors abort compilation.

    expect_target, when provided, is the skill directory name; a mismatch is a
    warning (never fatal) so old hand-written IRs keep working.
    """
    errors, warnings = [], []
    if not isinstance(ir, dict):
        return ["IR 必须是 JSON 对象"], warnings
    if str(ir.get("version")) != "1":
        errors.append("version 必须为 '1'")
    target = str(ir.get("target", ""))
    if not TARGET_IDENT.match(target):
        errors.append("target 缺失或不是合法标识符（目录名含点/空格时请用 "
                      "ir.slugify_target 规范化）")
    else:
        expected = slugify_target(expect_target) if expect_target else ""
        if expected and target != expected:
            warnings.append("target=%s 与目录名 %s 不一致，请核对是否编译了正确的 skill" % (
                target, expected))

    digest = ir.get("source_sha256", "")
    actual = sha256_text(source_text)
    if digest in ("", "auto"):
        warnings.append("source_sha256=auto：编译时绑定到当前源文件，生产环境建议写死摘要")
    elif not HEX64.match(str(digest)):
        errors.append("source_sha256 必须是 'auto' 或 64 位 sha256")
    elif digest != actual:
        errors.append("源 SKILL.md 已变更（sha256 不匹配）：重新 plan 并人审，不要直接重编译")

    # Gate: machine proposals must pass an explicit human approval step.
    review = ir.get("review_status")
    if review == "PROPOSED":
        errors.append("IR 仍是机器草案（review_status=PROPOSED）：先运行 `forge approve <dir> --yes` 完成人审")
    elif review not in (None, "APPROVED"):
        errors.append("review_status 只能是 APPROVED / PROPOSED，实际：%r" % review)
    elif review is None:
        warnings.append("IR 缺少 review_status：按 M0 人审 IR 兼容放行，建议用 `forge approve` 重新签发")

    allow_shell = bool(ir.get("allow_shell", False))
    allow_run_script = bool(ir.get("allow_run_script", False))
    allow_plugins = bool(ir.get("allow_plugins", False))
    plugin_ops = ir.get("plugin_ops", [])
    plugin_digest = ir.get("plugins_sha256", "")
    if not isinstance(plugin_ops, list) or any(
            not isinstance(n, str) or not PLUGIN_NAME.match(n) for n in plugin_ops):
        errors.append("plugin_ops 必须是小写蛇形插件名数组")
        plugin_ops = []
    if len(set(plugin_ops)) != len(plugin_ops):
        errors.append("plugin_ops 含重复项")
    if plugin_digest and not HEX64.match(str(plugin_digest)):
        errors.append("plugins_sha256 必须是 64 位 sha256")
    if plugin_ops and not plugin_digest:
        errors.append("声明了 plugin_ops 却缺少 plugins_sha256（插件必须哈希锁定）")
    if plugin_digest and not plugin_ops:
        errors.append("声明了 plugins_sha256 却缺少 plugin_ops 白名单")
    steps = ir.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps 必须是非空数组")
        return errors, warnings

    lines = source_text.splitlines()
    seen_ids, seen_emits, declared = set(), set(), set()
    decide_count = 0

    for idx, step in enumerate(steps, 1):
        tag = "steps[%d]" % idx
        if not isinstance(step, dict):
            errors.append("%s 必须是对象" % tag)
            continue
        if "proposal" in step:
            errors.append("%s 仍带 proposal 元数据（机器草案），须经 `forge approve` 剥离后才能编译" % tag)
        sid = str(step.get("id", ""))
        if not IDENT.match(sid):
            errors.append("%s.id 缺失或不是合法标识符" % tag)
        elif sid in seen_ids:
            errors.append("%s.id 重复: %s" % (tag, sid))
        seen_ids.add(sid)

        src = str(step.get("source", ""))
        m = SOURCE.match(src)
        if not m:
            errors.append("%s.source 缺失或格式错误（应为 SKILL.md:L12-L15）" % tag)
        else:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else start
            if start < 1 or end > len(lines) or end < start:
                errors.append("%s.source 行号越界: %s" % (tag, src))

        owner = step.get("owner")
        if owner not in ("code", "model"):
            errors.append("%s.owner 必须是 code 或 model" % tag)
            continue
        if owner == "model":
            if not step.get("note"):
                warnings.append("%s 是 model 步骤但缺少 note，精简版 SKILL.md 可能漏掉判断性指令" % tag)
            continue

        op = step.get("op")
        is_plugin = isinstance(op, str) and op.startswith(PLUGIN_PREFIX)
        plugin_name = op[len(PLUGIN_PREFIX):] if is_plugin else ""
        if op not in OPS and not (is_plugin and PLUGIN_NAME.match(plugin_name)):
            errors.append("%s.op 不在白名单 / 不是合法 plugin_<name>: %s" % (tag, op))
            continue
        args = step.get("args", {})
        if not isinstance(args, dict):
            errors.append("%s.args 必须是对象" % tag)
            continue
        if is_plugin:
            if plugin_name not in plugin_ops:
                errors.append("%s 使用的插件 %s 未列入 plugin_ops 白名单" % (tag, plugin_name))
            if not allow_plugins:
                errors.append("%s 使用插件 op，但 allow_plugins 未显式开启（默认 false）" % tag)
            elif not ir.get("approved_by"):
                errors.append("allow_plugins=true 时 IR 必须有 approved_by 签发人")
            elif ir.get("review_status") == "APPROVED" and \
                    ir.get("elevated_by") != ir.get("approved_by"):
                errors.append("allow_plugins 必须由 `forge approve --enable-plugins` "
                              "显式放通，且 elevated_by 必须与 approved_by 一致")
            for k, v in args.items():
                if not isinstance(k, str) or k.startswith("__"):
                    errors.append("%s.args 含非法参数名: %r" % (tag, k))
                elif not isinstance(v, (str, int, float, bool, type(None), list, dict)):
                    errors.append("%s.args.%s 必须是 JSON 标量/数组/对象" % (tag, k))
        else:
            extra = set(args) - OPS[op]
            if extra:
                errors.append("%s.args 含未声明键: %s" % (tag, ", ".join(sorted(extra))))
            _check_arg_types(tag, op, args, errors)

        emit = str(step.get("emit", ""))
        if not IDENT.match(emit):
            errors.append("%s.emit 缺失或不是合法标识符" % tag)
        elif emit.lower() in RESERVED_EMITS and not (op == "decide" and emit == "status"):
            errors.append("%s.emit 与结构化输出协议保留键冲突: %s" % (tag, emit))
        elif emit in seen_emits:
            errors.append("%s.emit 重复: %s" % (tag, emit))
        seen_emits.add(emit)

        switch = PRIVILEGED_OPS.get(op)
        if switch and not ir.get(switch, False):
            errors.append("%s 使用了 %s op，但 %s 未显式开启（默认 false）" % (tag, op, switch))
        if switch and ir.get(switch):
            if not ir.get("approved_by"):
                errors.append("%s=%s 时 IR 必须有 approved_by 签发人" % (switch, op))
            # A privileged switch may only be opened by an explicit human
            # `approve --enable-*`, never by an Agent patch or a hand-edit of
            # a machine-generated draft.
            if ir.get("review_status") == "APPROVED" and \
                    ir.get("elevated_by") != ir.get("approved_by"):
                errors.append("%s=%s 必须由 `forge approve --enable-%s` 显式放通，"
                              "且 elevated_by 必须与 approved_by 一致" % (
                                  switch, op, "shell" if op == "shell" else "run-script"))
            elif ir.get("review_status") != "APPROVED" and not ir.get("elevated_by"):
                warnings.append("%s=%s 的手工 IR 未记录 elevated_by，建议改用 "
                                "`forge approve --enable-%s` 签发" % (
                                    switch, op, "shell" if op == "shell" else "run-script"))

        if op in REF_OPS:
            ref = args.get("from")
            if ref not in declared:
                errors.append("%s.args.from 引用了未在前序步骤声明的变量: %s" % (tag, ref))
        if op == "decide":
            decide_count += 1
            if emit != "status":
                errors.append("%s 是 decide 步骤，emit 必须为 'status'" % tag)
            rules = args.get("rules")
            if isinstance(rules, list):
                for r in rules:
                    if not isinstance(r, dict):
                        continue
                    when = str(r.get("when", ""))
                    m2 = COND.match(when)
                    if not m2:
                        errors.append("%s 条件格式非法: %s" % (tag, when))
                        continue
                    var = m2.group(1)
                    if var not in declared:
                        errors.append("%s 条件引用了未在前序步骤声明的变量: %s" % (tag, var))
                    try:
                        _literal(m2.group(3))
                    except ValueError as exc:
                        errors.append("%s 条件右侧非法: %s" % (tag, exc))
        declared.add(emit)

    if decide_count > 1:
        errors.append("最多允许 1 个 decide 步骤")
    if decide_count == 0:
        warnings.append("没有 decide 步骤，STATUS 将恒为 OK")
    if not ir.get("kept_for_model"):
        warnings.append("kept_for_model 为空：该 skill 或许应整体改写为脚本，而非 Agent Skill")
    if not ir.get("prohibitions"):
        warnings.append("prohibitions 为空：精简版 SKILL.md 将缺少禁止动作声明")
    if allow_shell:
        warnings.append("allow_shell=true：请逐条人审 shell 命令")
    if allow_run_script:
        warnings.append("allow_run_script=true：run_script 可执行技能目录内的任意 Python，风险等级 L3，请逐条人审")
    if allow_plugins:
        warnings.append("allow_plugins=true：插件在受限命名空间内运行，风险等级 L3，"
                        "须逐条审过 plugins/*.py 并核对 plugins_sha256")
    banned = [x for x in ir.get("gates", []) or [] if x == "allow_network"]
    if banned:
        errors.append("gates 中出现 allow_network：本工具不支持放通网络")

    return errors, warnings


def load(path) -> dict:
    """Load JSON with UTF-8 BOM tolerance and a clear error message."""
    raw = Path(path).read_text(encoding="utf-8-sig")
    return json.loads(raw)
