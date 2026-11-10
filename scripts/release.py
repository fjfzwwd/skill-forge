"""Release engineering: pre-upload self-check and zip packaging.

`forge doctor` verifies the skill directory is publishable.
`forge package` produces a deterministic, self-verifying zip plus a MANIFEST
with per-file sha256, so an upload can be validated on the receiving side.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

import portability
from version import VERSION, RELEASE_DATE

REQUIRED = [
    "SKILL.md", "README.md", "PRD.md", "CHANGELOG.md", "LICENSE",
    "scripts/forge.py", "scripts/ir.py", "scripts/emitter.py",
    "scripts/core.py", "scripts/verifier.py", "scripts/proposer.py",
    "scripts/analyzer.py", "scripts/batch.py", "scripts/ci.py",
    "scripts/stability.py", "scripts/release.py", "scripts/version.py",
    "scripts/coverage_probe.py", "scripts/smoke.py",
    "scripts/bench.py", "scripts/oppack.py", "scripts/portability.py",
    "references/ir-schema.md", "references/owner-test.md",
    "references/structured-output.md", "references/safety-policy.md",
    "references/ci-integration.md", "references/agent-authoring.md",
    "references/stability-and-measurement.md", "references/benchmark-and-plugins.md",
    "templates/ir.template.json", "templates/agent-patch.json",
    "templates/ci/github-actions.yml", "templates/ci/gitlab-ci.yml",
    "templates/ci/pre-commit.sh",
    "evals/evals.json", "evals/cases/trigger-positive.yaml",
    "evals/cases/trigger-negative.yaml",
    "tests/test_forge.py",
    "examples/notes-audit/SKILL.md", "examples/notes-audit/ir.json",
    "examples/notes-audit-auto/SKILL.md",
    "examples/data-audit/SKILL.md", "examples/data-audit/agent-patch.json",
    "examples/data-audit/scripts/validate.py",
    "examples/data-audit/bench-fixtures.json",
    "examples/plugin-audit/SKILL.md", "examples/plugin-audit/agent-patch.json",
    "examples/plugin-audit/bench-fixtures.json",
    "examples/plugin-audit/plugins/risk_score.py",
]

EXCLUDE_PARTS = {
    "__pycache__", ".git", ".idea", ".vscode", ".workbuddy",
    "build", "dist", "reports", ".pytest_cache",
}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".tmp", ".bak", ".log"}
ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]Users[\\/]|/Users/|/home/|\\\\Users\\\\)")
SECRETISH = re.compile(r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9]{20,}|"
                       r"ghp_[A-Za-z0-9]{20,})")
# Paths that intentionally do not exist in the repository: compiled artifacts
# referenced from docs, and files belonging to external eval tools.
ALLOW_MISSING_REFS = {
    "scripts/crystallized.py", "evals/eval.yaml", "evals/cases/example.yaml",
    "examples/notes-audit/build", "examples/notes-audit-auto/build",
    "examples/data-audit/build", "examples/plugin-audit/build",
}
# This module contains the literal patterns it scans for; exempt it from its
# own leak check to avoid a self-inflicted false positive.
LEAK_SCAN_EXEMPT = {"scripts/release.py"}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _frontmatter(text: str) -> dict:
    """Parse the subset of YAML frontmatter used by skills.

    Handles folded/literal scalars (`>` / `|`) and one level of nested mapping
    (e.g. `metadata.version`), without a YAML dependency. Nested keys are
    exposed both as `parent.child` and as a bare name when unambiguous.
    """
    m = re.match(r"^\ufeff?---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    out = {}
    if not m:
        return out
    lines = m.group(1).splitlines()
    parent = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        mm = re.match(r"^(\s*)([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not mm:
            i += 1
            continue
        indent, key, val = mm.group(1), mm.group(2), mm.group(3).strip()
        if val in (">", "|", ">-", "|-"):
            parts = []
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                parts.append(lines[i].strip())
                i += 1
            val = (" ".join(x for x in parts if x) if val.startswith(">")
                   else "\n".join(parts))
        else:
            i += 1
        out[key] = val
        if indent:
            if parent:
                out[parent + "." + key] = val
            if key not in out:
                out[key] = val
        else:
            parent = "" if val else key
    return out


def release_files(root: Path):
    """Return sorted relative Paths of everything that goes into the zip."""
    files = set()
    for rel in REQUIRED:
        p = root / rel
        if p.is_file():
            files.add(Path(rel))
    # Include optional example data and any extra scripts/references/templates.
    for pattern in ("scripts/*.py", "references/*.md", "templates/ci/*",
                    "evals/cases/*", "examples/*/notes/*", "examples/*/data/*",
                    "examples/*/scripts/*", "examples/*/plugins/*",
                    "examples/*/*.json", "tests/*.py"):
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part in EXCLUDE_PARTS for part in rel.parts):
                continue
            if p.suffix in EXCLUDE_SUFFIX:
                continue
            files.add(rel)
    return sorted(files, key=lambda p: p.as_posix())


def doctor(root: Path):
    """Return (exit_code, lines)."""
    ok, bad = [], []

    def check(cond, good, worse):
        (ok if cond else bad).append(good if cond else worse)

    for rel in REQUIRED:
        check((root / rel).is_file(), "OK   %s" % rel, "MISS %s" % rel)

    skill = root / "SKILL.md"
    fm = _frontmatter(skill.read_text(encoding="utf-8")) if skill.is_file() else {}
    check(bool(fm.get("name")), "OK   frontmatter.name", "MISS frontmatter.name")
    desc = fm.get("description", "")
    check(80 <= len(desc) <= 1400,
          "OK   description 长度 %d" % len(desc),
          "BAD  description 长度 %d（建议 80..1400）" % len(desc))
    check(fm.get("license") == "MIT", "OK   license=MIT",
          "BAD  license 应为 MIT（实际 %r）" % fm.get("license"))

    # Version consistency across code and docs.
    vfile = root / "scripts" / "version.py"
    check(vfile.is_file() and ('VERSION = "%s"' % VERSION) in vfile.read_text(encoding="utf-8"),
          "OK   version.py = %s" % VERSION, "BAD  version.py 与 VERSION 不一致")
    for rel, needle in (("SKILL.md", "version: %s" % VERSION),
                        ("README.md", "v%s" % VERSION),
                        ("CHANGELOG.md", "## %s" % VERSION)):
        p = root / rel
        check(p.is_file() and needle in p.read_text(encoding="utf-8"),
              "OK   %s 标注 %s" % (rel, VERSION),
              "BAD  %s 缺少版本标注 '%s'" % (rel, needle))
    stale = []
    for p in sorted((root / "scripts").glob("*.py")):
        t = p.read_text(encoding="utf-8")
        if re.search(r"skill-forge (?:propose|batch|ci|measure) 0\.[12]\.\d", t):
            stale.append(p.name)
    check(not stale, "OK   无硬编码旧版本生成器",
          "BAD  旧版本生成器字符串: %s" % ", ".join(stale))

    # Syntax / JSON validity.
    py_bad = []
    for p in sorted(root.rglob("*.py")):
        if any(x in EXCLUDE_PARTS for x in p.relative_to(root).parts):
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            py_bad.append("%s: %s" % (p.name, exc))
    check(not py_bad, "OK   全部 .py 语法通过", "BAD  Python 语法: %s" % "; ".join(py_bad))
    portable = portability.scan(root)
    port_bad = ["%s: %s" % (f, "; ".join(x[:2])) for f, x in portable.items()]
    check(not portable, "OK   可移植性静态检查（encoding/路径/subprocess）",
          "BAD  可移植性: %s" % "; ".join(port_bad[:5]))
    js_bad = []
    for p in sorted(root.rglob("*.json")):
        if any(x in EXCLUDE_PARTS for x in p.relative_to(root).parts):
            continue
        try:
            json.loads(p.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            js_bad.append("%s: %s" % (p.name, exc))
    check(not js_bad, "OK   全部 .json 合法", "BAD  JSON: %s" % "; ".join(js_bad))

    # Referenced local paths actually exist.
    missing = []
    for p in release_files(root):
        if p.suffix not in (".md", ".py", ".yml", ".json", ".yaml", ".sh"):
            continue
        text = (root / p).read_text(encoding="utf-8", errors="ignore")
        for ref in re.findall(r"`((?:references|templates|examples|scripts|evals|tests)/[\w./-]+)`", text):
            if "*" in ref or ".." in ref or ref in ALLOW_MISSING_REFS:
                continue
            # Paths inside an example SKILL.md are relative to that example's
            # own directory (e.g. a helper in its own scripts folder), not to
            # the repository root.
            if not (root / ref).exists() and not (root / p.parent / ref).exists():
                missing.append("%s → %s" % (p.as_posix(), ref))
    check(not missing, "OK   文档内本地引用均存在",
          "BAD  引用缺失: %s" % "; ".join(sorted(set(missing))[:8]))

    # No machine-specific paths or secrets in shipped text files.
    leaks = []
    for p in release_files(root):
        if p.suffix not in (".md", ".py", ".yml", ".yaml", ".json", ".sh"):
            continue
        if p.as_posix() in LEAK_SCAN_EXEMPT:
            continue
        text = (root / p).read_text(encoding="utf-8", errors="ignore")
        if ABS_PATH.search(text):
            leaks.append(p.as_posix() + " (绝对路径)")
        if SECRETISH.search(text):
            leaks.append(p.as_posix() + " (疑似密钥)")
    check(not leaks, "OK   发行文件无本机绝对路径/疑似密钥",
          "BAD  泄漏: %s" % "; ".join(sorted(set(leaks))[:8]))

    caches = [p.as_posix() for p in root.rglob("__pycache__")]
    check(not caches, "OK   无 __pycache__ 残留",
          "WARN 存在 __pycache__（打包会排除）: %s" % ", ".join(caches[:3]))

    lines = ["skill-forge doctor · v%s · %s" % (VERSION, root.as_posix()), ""]
    lines += ["  " + x for x in ok]
    if bad:
        lines += ["", "需要处理："] + ["  " + x for x in bad]
    lines += ["", "%d 项通过 / %d 项失败" % (len(ok), len(bad)),
              "DOCTOR " + ("PASS" if not bad else "FAIL"), ""]
    return (0 if not bad else 1), lines


def package(root: Path, out_dir: Path = None):
    """Return (rc, lines, zip_path)."""
    files = release_files(root)
    if not files:
        return 2, ["没有可打包文件"], None
    missing = [f.as_posix() for f in files if not (root / f).is_file()]
    if missing:
        return 2, ["文件缺失: %s" % ", ".join(missing)], None

    out_dir = out_dir or (root / "dist")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / ("skill-forge-%s.zip" % VERSION)
    entries = []
    for rel in files:
        data = (root / rel).read_bytes()
        entries.append({
            "path": rel.as_posix(), "bytes": len(data),
            "sha256": sha256_bytes(data),
        })
    manifest = {
        "name": "skill-forge", "version": VERSION, "release_date": RELEASE_DATE,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "file_count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "files": entries,
        "verify": "python scripts/forge.py doctor . && python scripts/forge.py selftest .",
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("skill-forge/MANIFEST.json", manifest_bytes)
        for rel, e in zip(files, entries):
            zf.write(root / rel, "skill-forge/" + e["path"])

    # Read-back verification: CRC + sha256 of every member.
    problems = []
    with zipfile.ZipFile(zip_path) as zf:
        if zf.testzip() is not None:
            problems.append("zip CRC 校验失败")
        names = set(zf.namelist())
        for e in entries:
            key = "skill-forge/" + e["path"]
            if key not in names:
                problems.append("缺少成员 " + key)
            elif sha256_bytes(zf.read(key)) != e["sha256"]:
                problems.append("哈希不匹配 " + key)
        if "skill-forge/MANIFEST.json" not in names:
            problems.append("缺少 MANIFEST.json")

    (out_dir / "MANIFEST.json").write_bytes(manifest_bytes)
    skill_text = (root / "SKILL.md").read_text(encoding="utf-8")
    fm = _frontmatter(skill_text)
    upload = "\n".join([
        "# 上架信息 · skill-forge %s" % VERSION, "",
        "- 名称：`%s`" % fm.get("name", "skill-forge"),
        "- 版本：%s（%s）" % (VERSION, RELEASE_DATE),
        "- 许可：%s" % fm.get("license", "MIT"),
        "- 风险等级：L1（默认只读；开启 shell=L2，开启 run_script / plugin_* =L3）",
        "- 依赖：Python >= 3.9，仅标准库，无网络调用",
        "- 包内文件：%d 个，合计 %d 字节" % (len(entries), manifest["total_bytes"]),
        "- ZIP SHA256：`%s`" % sha256_bytes(zip_path.read_bytes()), "",
        "## 一句话", "",
        "把 SKILL.md 里确定性的重复步骤编译成自包含脚本，让模型只保留判断；"
        "带人审闸门、回归基线、漂移锁和 CI 门禁。", "",
        "## description（原样粘贴）", "", fm.get("description", ""), "",
        "## 建议标签", "",
        "`developer-tools` `agent-skills` `skill-authoring` `optimization` "
        "`static-analysis` `ci` `python` `chinese`", "",
        "## 安装后自检", "",
        "```bash",
        "cd skill-forge/scripts",
        "python forge.py --version",
        "python forge.py doctor .             # 64 项上架自检（含可移植性门禁）",
        "python forge.py selftest .           # 59 条内置回归",
        "python forge.py smoke . --no-zip     # 32 步干净目录端到端验收（推荐）",
        "",
        "# 路径 A：自动草案 → 人审 → 编译（最简）",
        "python forge.py propose ../examples/notes-audit-auto",
        "python forge.py approve ../examples/notes-audit-auto --yes --by <你的名字>",
        "python forge.py compile ../examples/notes-audit-auto",
        "python forge.py verify ../examples/notes-audit-auto --update-baseline",
        "python forge.py verify ../examples/notes-audit-auto --format jsonl",
        "python forge.py watch ../examples/notes-audit-auto",
        "",
        "# 路径 C：CSV + 技能自带脚本 + L3 显式放通 + 实测 + 编译前后对照",
        "python forge.py propose ../examples/data-audit",
        "python forge.py fill ../examples/data-audit --patch ../examples/data-audit/agent-patch.json --by <来源>",
        "python forge.py approve ../examples/data-audit --yes --by <你的名字> --enable-run-script",
        "python forge.py compile ../examples/data-audit",
        "python forge.py verify ../examples/data-audit --update-baseline",
        "python forge.py measure ../examples/data-audit --runs 3",
        "python forge.py bench ../examples/data-audit --runs 3",
        "python forge.py watch ../examples/data-audit",
        "",
        "# 路径 D：声明式 op 插件 + 哈希锁定 + 插件 L3 显式放通",
        "python forge.py propose ../examples/plugin-audit",
        "python forge.py fill ../examples/plugin-audit --patch ../examples/plugin-audit/agent-patch.json --by <来源>",
        "python forge.py approve ../examples/plugin-audit --yes --by <你的名字> --enable-plugins",
        "python forge.py compile ../examples/plugin-audit",
        "python forge.py verify ../examples/plugin-audit --update-baseline",
        "python forge.py bench ../examples/plugin-audit --runs 3",
        "python forge.py watch ../examples/plugin-audit",
        "```", "",
        "## 校验包完整性", "",
        "`MANIFEST.json` 内每个文件有 sha256；解压后逐一比对即可。"
        "包内不含 `build/` 与 `reports/`，示例产物由上述命令现场生成。", "",
        "若发行包是从 marketplace 下载后用 `forge smoke .`（不带 `--no-zip`）验收，"
        "会解压 `dist/` 里的最新 zip 到干净临时目录从零跑完整链路，含三条负例。"
        "`forge bench` 只做离线对照，不调用模型、不报告真实 token 与遵循率；"
        "`build/bench-cases.jsonl` 是接外部带模型 eval runner 的适配输入。", "",
    ]) + "\n"
    (out_dir / "UPLOAD.md").write_text(upload, encoding="utf-8")

    lines = [
        "打包：%d 个文件 / %d 字节 → %s" % (
            len(entries), manifest["total_bytes"], zip_path.as_posix()),
        "MANIFEST：%s" % (out_dir / "MANIFEST.json").as_posix(),
        "上架说明：%s" % (out_dir / "UPLOAD.md").as_posix(),
    ]
    if problems:
        lines += ["ERROR " + x for x in problems]
        lines.append("PACKAGE FAIL")
        return 1, lines, zip_path
    lines.append("回读校验：CRC + 逐文件 sha256 全部一致")
    lines.append("PACKAGE PASS")
    return 0, lines, zip_path
