"""Declared op plugins: static inspection, hash pinning, restricted runtime.

A plugin is a single `plugins/<name>.py` file inside the skill directory:

    SPEC = {"name": "risk_score", "args": ["weight"], "note": "..."}
    def run(ctx, **kwargs):
        return ...

Design rules:

  * Plugins are DISCOVERED, NEVER IMPORTED at compile time. Their metadata is
    read with `ast.literal_eval`, so a broken or hostile plugin cannot execute
    while `propose` / `compile` runs.
  * Every plugin file is hashed into `plugins_sha256` in the IR and the lock.
    Adding, editing or deleting a plugin file is drift, caught by `watch`.
  * Only names listed in `plugin_ops` may be called, and only args declared in
    that plugin's SPEC may be passed.
  * At runtime the plugin source is embedded into the generated runner and
    executed in a namespace with a restricted builtins set and a whitelisted
    importer (no `open`, no `os`, no `subprocess`, no `socket`). Plugins are
    meant to transform data the runner already read into `ctx`, not to do IO.

This is a *restricted namespace*, not a security sandbox: determined Python
can still escape via object introspection. Treat plugin code as trusted, read
it before signing, and keep `allow_plugins=false` unless you need it. L3.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

PLUGIN_DIR = "plugins"
PREFIX = "plugin_"
NAME = re.compile(r"^[a-z][a-z0-9_]*$")

# Modules a plugin may import inside the generated runner.
SAFE_MODULES = (
    "json", "re", "math", "csv", "io", "hashlib", "pathlib", "datetime",
    "collections", "statistics", "itertools", "functools", "string",
    "decimal", "fractions", "unicodedata", "base64", "binascii", "copy",
    "operator", "textwrap", "enum", "types", "bisect", "heapq",
)
# Builtins exposed to plugin code. Deliberately excludes open/exec/eval/
# compile/__import__/getattr/object introspection and any IO primitive.
SAFE_BUILTINS = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "int", "isinstance", "issubclass", "len",
    "list", "map", "max", "min", "pow", "range", "repr", "reversed", "round",
    "set", "slice", "sorted", "str", "sum", "tuple", "zip", "complex", "bytes",
    "bytearray", "ord", "chr", "hex", "oct", "bin", "hash", "iter", "next",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "RuntimeError", "ArithmeticError", "ZeroDivisionError", "StopIteration",
    "True", "False", "None",
)


def _spec_from_source(src: str, where: str):
    tree = ast.parse(src, where)
    spec, run_node = None, None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SPEC":
                    try:
                        spec = ast.literal_eval(node.value)
                    except Exception as exc:
                        raise ValueError("SPEC 必须是字面量对象：%s (%s)" % (where, exc))
        elif isinstance(node, ast.FunctionDef) and node.name == "run":
            run_node = node
    if not isinstance(spec, dict):
        raise ValueError("%s 缺少 SPEC 字典" % where)
    name = spec.get("name")
    if not isinstance(name, str) or not NAME.match(name):
        raise ValueError("%s 的 SPEC.name 必须是小写蛇形标识符，实际 %r" % (where, name))
    args = spec.get("args", [])
    if not isinstance(args, (list, tuple)) or any(not isinstance(a, str)
                                                  or not re.match(r"^[A-Za-z_]\w*$", a)
                                                  for a in args):
        raise ValueError("%s 的 SPEC.args 必须是合法字符串数组" % where)
    if run_node is None:
        raise ValueError("%s 缺少 run(ctx, **kwargs) 函数" % where)
    if not run_node.args.args or run_node.args.args[0].arg != "ctx":
        raise ValueError("%s 的 run() 第一个参数必须是 ctx" % where)
    return {"name": name, "args": list(args), "note": str(spec.get("note", ""))}


def discover(d: Path):
    """Return (plugins, errors). Never imports plugin code."""
    base = d / PLUGIN_DIR
    if not base.is_dir():
        return [], []
    plugins, errors, seen = [], [], set()
    for p in sorted(base.glob("*.py")):
        rel = p.relative_to(d).as_posix()
        try:
            src = p.read_text(encoding="utf-8-sig")
            meta = _spec_from_source(src, rel)
        except (OSError, SyntaxError, ValueError) as exc:
            errors.append("插件 %s 不可用：%s" % (rel, exc))
            continue
        if meta["name"] in seen:
            errors.append("插件名重复：%s（%s）" % (meta["name"], rel))
            continue
        seen.add(meta["name"])
        plugins.append({
            "name": meta["name"], "args": meta["args"], "note": meta["note"],
            "file": rel, "source": src,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        })
    return plugins, errors


def digest(plugins) -> str:
    """Hash over file path + declared name + bytes; small changes are drift."""
    rows = ["%s:%s:%s" % (p["file"], p["name"], p["sha256"]) for p in plugins]
    return hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()


def declared_digest(d: Path) -> str:
    """Digest of the plugins currently on disk (used by `watch`)."""
    plugins, _ = discover(d)
    return digest(plugins)


HASH_OF_NOTHING = digest([])


def check(d: Path, ir: dict):
    """Verify an approved IR against the actual plugin directory.

    Returns (errors, plugins). Called from `compile_skill` after IR.validate,
    which has already checked the IR-level whitelist and signature.
    """
    steps = [s for s in ir.get("steps", []) if isinstance(s, dict)]
    used = {str(s.get("op", ""))[len(PREFIX):] for s in steps
            if str(s.get("op", "")).startswith(PREFIX)}
    if not used and not ir.get("allow_plugins"):
        return [], []
    plugins, errors = discover(d)
    if errors:
        return errors, plugins
    by_name = {p["name"]: p for p in plugins}
    missing = sorted(used - set(by_name))
    if missing:
        errors.append("IR 引用了不存在的插件：%s" % ", ".join(missing))
    declared = {str(x) for x in (ir.get("plugin_ops", []) or [])}
    unknown = sorted(declared - set(by_name))
    if unknown:
        errors.append("plugin_ops 声明了 plugins/ 中不存在的项目：%s" % ", ".join(unknown))
    undeclared = sorted(used - declared)
    if undeclared:
        errors.append("IR 使用了未列入 plugin_ops 的插件：%s" % ", ".join(undeclared))
    expected = ir.get("plugins_sha256", "")
    actual = digest(plugins)
    if expected != actual:
        errors.append("plugins/ 内容已变更（sha256 不匹配）：重新 propose → 人审 → compile，不要直接重编译")
    for s in steps:
        op = str(s.get("op", ""))
        if not op.startswith(PREFIX) or op[len(PREFIX):] not in by_name:
            continue
        meta = by_name[op[len(PREFIX):]]
        args = s.get("args", {}) or {}
        extra = set(map(str, args)) - set(meta["args"])
        if extra:
            errors.append("%s 传入了插件未声明的参数：%s" % (
                s.get("id", op), ", ".join(sorted(extra))))
    return errors, plugins


def plugin_block(plugins) -> str:
    """Python source embedded into the generated runner."""
    sources = "{\n" + "".join(
        "    %r: %r,\n" % (p["name"], p["source"]) for p in plugins) + "}"
    return """# --- declared op plugins (L3, hash-pinned by the IR) -----------------
import builtins as _builtins

_SAFE_MODULES = frozenset(%r)
_SAFE_BUILTIN_NAMES = %r

def _plugin_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in _SAFE_MODULES:
        raise ImportError("plugin import blocked: " + name)
    return _builtins.__import__(name, globals, locals, fromlist, level)


def _plugin_builtins():
    out = {n: getattr(_builtins, n) for n in _SAFE_BUILTIN_NAMES
           if hasattr(_builtins, n)}
    out["__import__"] = _plugin_import
    return out


PLUGIN_SOURCES = %s


def _load_plugins():
    reg = {}
    for name, src in PLUGIN_SOURCES.items():
        ns = {"__builtins__": _plugin_builtins(),
              "__name__": "sf_plugin_" + name}
        exec(compile(src, "plugins/" + name + ".py", "exec"), ns)
        fn = ns.get("run")
        if not callable(fn):
            raise ValueError("plugin missing run(): " + name)
        reg[name] = fn
    return reg


PLUGINS = _load_plugins() if ALLOW_PLUGINS else {}


def op_plugin(ctx, name, **kwargs):
    if not ALLOW_PLUGINS:
        raise ValueError("plugin op disabled by policy")
    fn = PLUGINS.get(name)
    if fn is None:
        raise ValueError("plugin not declared: " + name)
    return fn(ctx, **kwargs)
# ---------------------------------------------------------------------
""" % (tuple(SAFE_MODULES), tuple(SAFE_BUILTINS), sources)
