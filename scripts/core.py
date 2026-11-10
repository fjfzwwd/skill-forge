"""Shared operations for the CLI, batch mode and CI gate.

All functions return (exit_code, messages/output) instead of printing, so the
same semantics are enforced in interactive, batch and CI contexts.
"""
from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

import ir as IR
import analyzer
import emitter
import verifier
import proposer
import stability
import oppack
from version import VERSION


def skill_dir(raw) -> Path:
    p = Path(raw).resolve()
    if not p.is_dir():
        raise SystemExit("目录不存在：%s" % p)
    if not (p / "SKILL.md").is_file():
        raise SystemExit("找不到 SKILL.md：%s" % p)
    return p


def read_skill(d: Path) -> str:
    return (d / "SKILL.md").read_text(encoding="utf-8-sig")


def _drift_guard(d: Path, text: str):
    """Refuse to recompile over a different source than the one locked.

    `source_sha256: auto` in a hand-written IR would otherwise silently bind a
    brand-new document, which is exactly the "quiet divergence" this tool
    exists to prevent.
    """
    lock = d / "build" / "crystallize.lock.json"
    if not lock.is_file():
        return None
    try:
        info = json.loads(lock.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return "锁文件损坏：%s（请删除 build/ 后重新编译）" % lock
    if info.get("source_sha256") != IR.sha256_text(text):
        return ("源 SKILL.md 与上次编译的锁文件不一致。"
                "请先 `forge watch` 确认，再走 propose → 人审 → compile；"
                "确需覆盖请加 --force（会留痕）")
    return None


def compile_skill(d: Path, ir_path=None, force=False, skip_guard=False):
    """Return (rc, lines). Artifacts only go to build/."""
    text = read_skill(d)
    p = Path(ir_path).resolve() if ir_path else d / "build" / "ir.json"
    lines = []
    if not p.is_file():
        lines.append("缺少 IR：%s" % p)
        lines.append("先运行 `forge propose` + `forge approve --yes`，或用 --ir 指定人审 IR")
        return 2, lines
    try:
        data = IR.load(p)
    except Exception as exc:
        lines.append("ERROR IR 无法解析：%s" % exc)
        return 2, lines
    errors, warnings = IR.validate(data, text, expect_target=d.name)
    for w in warnings:
        lines.append("WARN  " + w)
    if errors:
        for e in errors:
            lines.append("ERROR " + e)
        lines.append("编译中止：%d 个门禁失败" % len(errors))
        return 2, lines

    plugin_errors, plugins = oppack.check(d, data)
    if plugin_errors:
        for e in plugin_errors:
            lines.append("ERROR " + e)
        lines.append("编译中止：插件校验失败（%d 项）" % len(plugin_errors))
        return 2, lines

    drift = _drift_guard(d, text)
    if drift and not force:
        lines.append("ERROR " + drift)
        return 2, lines
    if drift and force:
        lines.append("WARN  源文档漂移已被 --force 覆盖，已记录到 build/")

    if not skip_guard:
        allowed, status, reason, detail = stability.guard(d, force=force)
        lines.append("GUARD %s %s" % (status, reason))
        if not allowed:
            lines.append("ERROR NOT_READY：拒绝编译。流程形状未稳定时固化代码风险更高。")
            lines.append("       先采样 `forge measure <dir> --runs 5 --record`，或确需继续加 --force（留痕）。")
            return 2, lines

    src_sha = IR.sha256_text(text)
    ir_sha = IR.sha256_text(json.dumps(data, ensure_ascii=False, sort_keys=True))
    build = d / "build"
    (build / "scripts").mkdir(parents=True, exist_ok=True)
    (build / "fixtures").mkdir(parents=True, exist_ok=True)
    (build / "backup").mkdir(parents=True, exist_ok=True)
    # Keep the ORIGINAL backup. Overwriting it on every compile would destroy
    # the only copy of the source once a human has replaced SKILL.md.
    backup = build / "backup" / "SKILL.md"
    if not backup.is_file():
        shutil.copy2(d / "SKILL.md", backup)
    if drift and force:
        (build / "override-audit.json").write_text(json.dumps({
            "at": datetime.now().isoformat(timespec="seconds"),
            "generator": "skill-forge %s" % VERSION,
            "from_source_sha256": json.loads(
                (build / "crystallize.lock.json").read_text(encoding="utf-8")
            ).get("source_sha256", ""),
            "to_source_sha256": src_sha,
            "reason": "compile --force over a drifted source",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (build / "scripts" / "crystallized.py").write_text(
        emitter.render_runner(data, "../..", plugins), encoding="utf-8")
    minimal = emitter.render_minimal_skill(data, text)
    (build / "SKILL.md").write_text(minimal, encoding="utf-8")
    (build / "report.md").write_text(
        emitter.render_report(data, text, minimal), encoding="utf-8")
    final = dict(data)
    final["source_sha256"] = src_sha
    (build / "ir.final.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (build / "crystallize.lock.json").write_text(
        emitter.render_lock(data, src_sha, ir_sha, emitter.hash_tree(build)), encoding="utf-8")

    lines.append("OK 编译完成：%d 个 code 步骤，%d 个 model 步骤（skill-forge %s）" % (
        sum(1 for s in data["steps"] if s.get("owner") == "code"),
        sum(1 for s in data["steps"] if s.get("owner") == "model"), VERSION))
    for name in ("scripts/crystallized.py", "SKILL.md", "report.md",
                 "ir.final.json", "crystallize.lock.json"):
        lines.append("  build/" + name)
    lines.append("源 SKILL.md 未改动。人审后再手工替换；用 `forge verify --update-baseline` 建立基线。")
    return 0, lines


def verify_skill(d: Path, update=False, fmt="text", root=None, record=False):
    build = d / "build"
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = verifier.verify(build, update, fmt, str(d) if root else None)
    out = buf.getvalue().rstrip()
    if record and rc == 0:
        import time
        t0 = time.time()
        try:
            proc = verifier.run(build / "scripts" / "crystallized.py",
                                str(d) if root else None)
            stability.record(d, proc.stdout, int((time.time() - t0) * 1000))
        except Exception:
            pass  # sampling is best-effort; never break a passing verify
    return rc, out


def watch_skill(d: Path):
    lock = d / "build" / "crystallize.lock.json"
    if not lock.is_file():
        return 2, "NO_LOCK 尚未编译"
    try:
        info = json.loads(lock.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return 1, "DRIFT 锁文件损坏：%s" % exc
    if IR.sha256_text(read_skill(d)) != info.get("source_sha256"):
        return 1, "DRIFT 源 SKILL.md 已变更，需重新 propose → 人审 → compile（不要自动重编译）"
    bad = []
    for rel, digest in info.get("files", {}).items():
        p = d / "build" / rel
        if not p.is_file() or IR.sha256_file(p) != digest:
            bad.append(rel)
    if bad:
        return 1, "DRIFT 生成产物被改动：" + ", ".join(bad)
    if info.get("plugins_sha256") and \
            info["plugins_sha256"] != oppack.declared_digest(d):
        return 1, ("DRIFT plugins/ 内容自编译后已变更；"
                   "重新 propose → 人审 → compile（不要直接重编译）")
    msg = "CLEAN 源文档与生成产物均与锁文件一致"
    valid, _, oreason = stability.override_state(d)
    if valid:
        msg += "；注意：NOT_READY 强制豁免生效中（%s）" % oreason
    return 0, msg


def propose_skill(d: Path, write=True):
    """Return (rc, lines, draft)."""
    text = read_skill(d)
    cands = analyzer.write_scan(d, text)
    draft = proposer.propose(d, text, cands)
    lines = [
        "推断 code 步骤 %d 条 / model 步骤 %d 条；未映射 %d 行" % (
            draft["proposal"]["inferred_code_steps"],
            sum(1 for s in draft["steps"] if s.get("owner") == "model"),
            len(draft["proposal"]["unmapped"]),
        ),
    ]
    for w in draft["proposal"]["warnings"]:
        lines.append("WARN  " + w)
    if write:
        build = d / "build"
        build.mkdir(parents=True, exist_ok=True)
        out = build / "ir.proposed.json"
        out.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sheet = build / "review-sheet.md"
        sheet.write_text(proposer.review_sheet(draft), encoding="utf-8")
        lines.append("草案：%s" % out)
        lines.append("人审清单：%s" % sheet)
        lines.append("草案不可编译。下一步：`forge approve %s --yes --by <审阅者>`" % d.name)
    return 0, lines, draft


def fill_skill(d: Path, patch_path: Path, by: str = "agent"):
    """Merge an Agent-authored patch into the PROPOSED draft.

    The patch never bypasses human approval: the result is still PROPOSED.
    A full IR validation of the merged steps is reported before writing.
    """
    draft_path = d / "build" / "ir.proposed.json"
    if not draft_path.is_file():
        return 2, ["缺少草案：%s（先运行 `forge propose`）" % draft_path], None
    try:
        patch = IR.load(patch_path)
    except Exception as exc:
        return 2, ["ERROR 补丁无法解析：%s" % exc], None
    if not isinstance(patch, dict):
        return 2, ["ERROR 补丁必须是 JSON 对象"], None
    try:
        draft = IR.load(draft_path)
    except Exception as exc:
        return 2, ["ERROR 草案无法解析：%s" % exc], None

    allowed_keys = {"steps_add", "steps_replace", "steps_remove",
                    "prohibitions_add", "kept_for_model_add",
                    "allow_shell", "allow_run_script", "allow_plugins",
                    "plugin_ops", "plugins_sha256", "description", "by"}
    # Keys starting with "_" are documentation-only (e.g. "_hint" in the
    # shipped template) and must never break an otherwise valid patch.
    unknown = {k for k in set(patch) - allowed_keys if not k.startswith("_")}
    if unknown:
        return 2, ["ERROR 补丁含未知键：%s" % ", ".join(sorted(unknown))], None

    # Privilege escalation must never originate from a machine-authored patch.
    # Opening L2/L3 is an explicit human act at `approve` time, with a name
    # recorded in elevated_by. A patch may only tighten (set false).
    escalated = [k for k in ("allow_shell", "allow_run_script", "allow_plugins")
                 if patch.get(k) is True]
    if escalated:
        return 2, [
            "ERROR 补丁试图开启特权开关：%s" % ", ".join(escalated),
            "       Agent 补丁不得自我提权。若确需 shell / run_script / plugins，",
            "       由人审执行 `forge approve <dir> --yes --by <姓名> "
            "--enable-shell` / `--enable-run-script` / `--enable-plugins`。",
            "       补丁保持不变。",
        ], None

    steps = list(draft.get("steps", []))
    for sid in patch.get("steps_remove", []):
        steps = [s for s in steps if s.get("id") != sid]
    replaces = patch.get("steps_replace", {})
    if not isinstance(replaces, dict):
        return 2, ["ERROR steps_replace 必须是对象"], None
    known_ids = {s.get("id") for s in steps if isinstance(s, dict)}
    steps = [replaces.get(s.get("id"), s) if isinstance(s, dict) else s for s in steps]
    for s in patch.get("steps_add", []):
        if not isinstance(s, dict):
            return 2, ["ERROR steps_add 每项必须是对象"], None
        steps.append(s)

    for s in steps:
        if isinstance(s, dict) and "proposal" not in s:
            s["proposal"] = {"confidence": "agent", "reason": "由 Agent 补丁提供，需人审",
                             "matched_text": ""}
    draft["steps"] = steps
    for key, src in (("prohibitions", "prohibitions_add"),
                     ("kept_for_model", "kept_for_model_add")):
        for x in patch.get(src, []):
            if x not in draft.get(key, []):
                draft.setdefault(key, []).append(x)
    for key in ("allow_shell", "allow_run_script", "allow_plugins",
                "plugin_ops", "plugins_sha256", "description"):
        if key in patch:
            draft[key] = patch[key]
    draft["patched_by"] = patch.get("by") or by
    draft["patched_at"] = datetime.now().isoformat(timespec="seconds")
    draft.setdefault("proposal", {})["agent_patch"] = {
        "added": len(patch.get("steps_add", [])),
        "replaced": len(replaces),
        "removed": len(patch.get("steps_remove", [])),
        "source": Path(patch_path).name,
    }

    # Validate the merged steps without tripping the (intentional) PROPOSED gate.
    # The probe carries a *throwaway* signature: it exists only so that a patch
    # adding a privileged op (run_script / shell) can be structurally checked.
    # It is never written to the draft -- the real signature comes from approve.
    probe = dict(draft)
    probe["review_status"] = "APPROVED"
    probe["approved_by"] = "__validation_probe__"
    probe["elevated_by"] = "__validation_probe__"
    probe["steps"] = [{k: v for k, v in s.items() if k != "proposal"}
                      if isinstance(s, dict) else s for s in steps]
    # Structural validation only: temporarily mirror whichever privileged ops
    # the merged steps contain, so a patch adding run_script can be checked for
    # path/type safety without claiming the L3 switch is actually open. The
    # real grant is decided by the human at `approve --enable-*`.
    probe["allow_shell"] = any(s.get("op") == "shell" for s in probe["steps"]
                               if isinstance(s, dict))
    probe["allow_run_script"] = any(s.get("op") == "run_script" for s in probe["steps"]
                                    if isinstance(s, dict))
    probe["allow_plugins"] = any(str(s.get("op", "")).startswith(oppack.PREFIX)
                                 for s in probe["steps"] if isinstance(s, dict))
    errors, warnings = IR.validate(probe, read_skill(d), expect_target=d.name)
    lines = ["补丁合并：新增 %d / 替换 %d / 删除 %d 步" % (
        len(patch.get("steps_add", [])), len(replaces),
        len(patch.get("steps_remove", [])))]
    orphan = sorted(set(replaces) - known_ids)
    if orphan:
        lines.append("WARN  steps_replace 中这些 id 在草案里不存在，已忽略：%s"
                     % ", ".join(orphan))
    privileged_added = [s.get("op") for s in patch.get("steps_add", [])
                        if isinstance(s, dict) and (
                            s.get("op") in ("shell", "run_script")
                            or str(s.get("op", "")).startswith(oppack.PREFIX))]
    if privileged_added:
        lines.append("WARN  补丁新增了特权步骤 %s：草案开关仍为 false，签发时须显式 "
                     "`approve --enable-run-script` / `--enable-shell` / "
                     "`--enable-plugins`" % ", ".join(privileged_added))
    # The probe had to mirror the switches for structural validation; do not
    # let those synthetic warnings look like a real grant.
    warnings = [w for w in warnings
                if not w.startswith(("allow_shell=true", "allow_run_script=true",
                                     "allow_plugins=true"))]
    for w in warnings:
        lines.append("WARN  " + w)
    if errors:
        for e in errors:
            lines.append("ERROR " + e)
        lines.append("补丁未写入：修正后重试（草案保持不变）")
        return 2, lines, None

    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    (d / "build" / "review-sheet.md").write_text(
        proposer.review_sheet(draft), encoding="utf-8")
    (d / "build" / "agent-patch.applied.json").write_text(
        json.dumps(patch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines.append("OK 补丁已合并，草案仍为 PROPOSED（不绕过人审）：%s" % draft_path)
    lines.append("下一步：打开 review-sheet.md 核对，再 `forge approve --yes --by <审阅者>`")
    return 0, lines, draft


def report_skill(d: Path, measurement_path=None):
    """Merge real measurements into a report that is NOT part of the lock."""
    build = d / "build"
    irp = build / "ir.final.json"
    mp = Path(measurement_path).resolve() if measurement_path \
        else build / "measurement.json"
    if not irp.is_file():
        return 2, ["缺少编译产物：%s（先 `forge compile`）" % irp]
    if not mp.is_file():
        return 2, ["缺少实测数据：%s（先 `forge measure <dir> --runs N`）" % mp]
    try:
        data = json.loads(irp.read_text(encoding="utf-8-sig"))
        measurement = json.loads(mp.read_text(encoding="utf-8-sig"))
        minimal = (build / "SKILL.md").read_text(encoding="utf-8-sig")
    except (ValueError, OSError) as exc:
        return 2, ["ERROR 无法读取报告输入：%s" % exc]
    out = build / "report.measured.md"
    out.write_text(emitter.render_measured_report(
        data, read_skill(d), minimal, measurement), encoding="utf-8")
    return 0, ["OK 实测报告已生成：%s" % out,
               "该文件不进漂移锁，`forge watch` 仍应为 CLEAN。"]


def approve_skill(d: Path, ir_path=None, out=None, reviewer="human", yes=False,
                  enable_shell=False, enable_run_script=False,
                  enable_plugins=False):
    """Explicit human approval. Without --yes this only prints the checklist.

    Privilege switches are reset on every approval and may only be reopened by
    the three explicit flags below. This is the single place where L2/L3 can be
    granted: a machine draft or an Agent patch can never carry them through.
    """
    p = Path(ir_path).resolve() if ir_path else d / "build" / "ir.proposed.json"
    if not p.is_file():
        return 2, ["缺少草案：%s（先运行 `forge propose`）" % p]
    try:
        data = IR.load(p)
    except Exception as exc:
        return 2, ["ERROR 草案无法解析：%s" % exc]
    sheet = proposer.review_sheet(data)
    lines = [sheet]
    if not yes:
        lines.append("未签发：以上清单需人工确认，确认后加 `--yes [--by <姓名>]`。")
        return 1, lines
    if data.get("review_status") not in (None, "PROPOSED"):
        return 2, lines + ["ERROR 已是正式 IR，无需重复 approve"]
    if reviewer.strip() in ("", "human"):
        lines.append("WARN  使用默认审阅者 'human'；建议 --by 写明姓名以便审计")
    if data.get("source_sha256") not in ("", "auto") \
            and data.get("source_sha256") != IR.sha256_text(read_skill(d)):
        lines.append("WARN  草案绑定的源摘要与当前 SKILL.md 不一致，compile 会拒绝；建议重新 propose")

    ops = {str(s.get("op", "")) for s in data.get("steps", []) if isinstance(s, dict)}
    plugin_ops = {o for o in ops if o.startswith(oppack.PREFIX)}
    needs = []
    if "shell" in ops and not enable_shell:
        needs.append("shell（需 --enable-shell）")
    if "run_script" in ops and not enable_run_script:
        needs.append("run_script（需 --enable-run-script）")
    if plugin_ops and not enable_plugins:
        needs.append("plugin（需 --enable-plugins）")
    if needs:
        return 2, lines + ["ERROR IR 含特权步骤但未显式放通：%s" % "、".join(needs),
                           "       Agent 补丁不能自我提权；请由人审带对应参数重新签发。"]
    unused = []
    if enable_shell and "shell" not in ops:
        unused.append("--enable-shell")
    if enable_run_script and "run_script" not in ops:
        unused.append("--enable-run-script")
    if enable_plugins and not plugin_ops:
        unused.append("--enable-plugins")
    if unused:
        return 2, lines + ["ERROR IR 中没有对应特权步骤，拒绝无谓放通：%s" % "、".join(unused)]

    approved = proposer.approve(data, reviewer)
    # Unconditional reset: whatever the draft claimed is irrelevant.
    approved["allow_shell"] = bool(enable_shell)
    approved["allow_run_script"] = bool(enable_run_script)
    approved["allow_plugins"] = bool(enable_plugins)
    approved.pop("elevated_by", None)
    approved.pop("elevated_at", None)
    if enable_shell or enable_run_script or enable_plugins:
        approved["elevated_by"] = reviewer
        approved["elevated_at"] = datetime.now().isoformat(timespec="seconds")
        level = "L3" if (enable_run_script or enable_plugins) else "L2"
        lines.append("WARN  人工放通 %s：%s（审阅者 %s 已记入 elevated_by）" % (
            level, ", ".join(x for x, on in (
                ("allow_shell", enable_shell),
                ("allow_run_script", enable_run_script),
                ("allow_plugins", enable_plugins)) if on), reviewer))
    target = Path(out).resolve() if out else d / "build" / "ir.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(approved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines.append("APPROVED by %s → %s" % (reviewer, target))
    lines.append("下一步：`forge compile %s`（默认读取 build/ir.json）" % d.name)
    return 0, lines
