"""Offline before/after benchmark and eval-runner adapter.

What this measures (and, more importantly, what it does NOT):

  MEASURED   prompt volume of the original vs. the crystallized SKILL.md
             (chars / words / lines, plus an exact count when a local
             tokenizer is installed); runner determinism over N runs;
             elapsed time; procedural conformance against declarative
             fixtures; tool-call count reduction (structural, from the IR).

  NOT MEASURED   real model token consumption, model step-adherence, answer
             quality, or cost. Those require a model-in-the-loop eval runner.
             `bench-cases.jsonl` is the adapter: each case carries both
             prompts, the scenario inputs and the expected structured output,
             so an external runner can run the A/B without guessing.

No network access, no third-party requirement, no writes outside build/.
"""
from __future__ import annotations

import json
import shutil
import statistics
import tempfile
from datetime import datetime
from pathlib import Path

import ir as IR
import verifier
from version import VERSION

GENERATOR = "skill-forge bench %s" % VERSION
META_KEYS = {"ELAPSED_MS", "STEPS"}
SCOPE_NOTE = (
    "离线确定性基准：测量 prompt 体积、运行器确定性、耗时与声明式用例符合度。"
    "不测量真实模型 token、遵循率或答案质量；那需要带模型的 eval runner，"
    "可用 build/bench-cases.jsonl 作为输入。"
)


def volume(text: str) -> dict:
    return {
        "chars": len(text),
        "chars_no_space": len("".join(text.split())),
        "words": len(text.split()),
        "lines": len(text.splitlines()),
        "bytes_utf8": len(text.encode("utf-8")),
    }


def token_count(text: str) -> dict:
    """Exact count only when a local tokenizer is importable; never download."""
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("cl100k_base")
        return {"method": "tiktoken:cl100k_base", "count": len(enc.encode(text))}
    except Exception:
        pass
    try:
        import tokenizers  # type: ignore  # noqa: F401
        return {"method": "unavailable:no-compatible-encoding", "count": None}
    except Exception:
        pass
    return {"method": "unavailable:no-local-tokenizer", "count": None}


def _copy_skill(d: Path, work: Path) -> None:
    """Copy the data a runner needs, excluding all generated artifacts."""
    def ignore(_dir, names):
        return {n for n in names if n in ("build", "__pycache__", ".git",
                                          ".workbuddy", "dist", "reports")}
    shutil.copytree(d, work, ignore=ignore, dirs_exist_ok=True)


def _apply_overrides(work: Path, case: dict) -> list:
    errors = []
    files = case.get("files", {}) or {}
    if not isinstance(files, dict):
        return ["files 必须是对象"]
    for rel, content in files.items():
        if IR.bad_path(str(rel)):
            errors.append("覆盖路径越界：%s" % rel)
            continue
        if not isinstance(content, str):
            errors.append("覆盖内容必须是字符串：%s" % rel)
            continue
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    for rel in case.get("remove", []) or []:
        if IR.bad_path(str(rel)):
            errors.append("删除路径越界：%s" % rel)
            continue
        p = work / rel
        if p.is_file():
            p.unlink()
    return errors


def _run_case(runner: Path, d: Path, case: dict, timeout: int = 60) -> dict:
    cid = str(case.get("id", "unnamed"))
    expect = case.get("expect", {}) or {}
    with tempfile.TemporaryDirectory(prefix="sf-bench-") as tmp:
        work = Path(tmp) / "skill"
        _copy_skill(d, work)
        errors = _apply_overrides(work, case)
        if errors:
            return {"case_id": cid, "passed": False, "detail": "; ".join(errors),
                    "expected": expect, "observed": {}, "diffs": []}
        try:
            proc = verifier.run(runner, str(work), timeout=timeout)
        except Exception as exc:
            return {"case_id": cid, "passed": False,
                    "detail": "运行失败：%s" % exc,
                    "expected": expect, "observed": {}, "diffs": []}
        kv = verifier.parse_kv(proc.stdout)
        diffs = []
        for k, v in expect.items():
            got = kv.get(str(k).upper())
            if got != str(v):
                diffs.append({"key": k, "expected": str(v), "observed": got})
        error_expected = str(expect.get("STATUS", "")) == "ERROR"
        rc_ok = (proc.returncode != 0) if error_expected else (proc.returncode == 0)
        status_ok = (kv.get("STATUS") == "ERROR") if error_expected \
            else (kv.get("STATUS") != "ERROR")
        passed = not diffs and rc_ok and status_ok
        return {
            "case_id": cid,
            "note": str(case.get("note", "")),
            "passed": passed,
            "returncode": proc.returncode,
            "expected": expect,
            "observed": {k: v for k, v in kv.items() if k not in META_KEYS},
            "diffs": diffs,
            "detail": ("PASS 符合声明式期望" if passed else
                       "FAIL %s" % ("; ".join(
                           "%s 期望 %r 实际 %r" % (x["key"], x["expected"], x["observed"])
                           for x in diffs) or "退出码/STATUS 与期望不符")),
        }


def _determinism(runner: Path, d: Path, runs: int) -> dict:
    samples = []
    for _ in range(max(1, runs)):
        try:
            proc = verifier.run(runner, str(d))
            kv = verifier.parse_kv(proc.stdout)
            samples.append({
                "ok": proc.returncode == 0 and kv.get("STATUS") != "ERROR",
                "shape": {k: v for k, v in kv.items() if k not in META_KEYS},
                "elapsed_ms": int(kv.get("ELAPSED_MS") or 0),
                "output_bytes": len(proc.stdout.encode("utf-8")),
                "error": kv.get("ERROR", ""),
            })
        except Exception as exc:
            samples.append({"ok": False, "shape": {}, "elapsed_ms": 0,
                            "output_bytes": 0, "error": str(exc)})
    shapes = [s["shape"] for s in samples]
    times = [s["elapsed_ms"] for s in samples if s["ok"]] or [0]
    return {
        "requested_runs": max(1, runs),
        "successful_runs": sum(1 for s in samples if s["ok"]),
        "shape_stable": bool(shapes) and all(s == shapes[0] for s in shapes),
        "elapsed_ms_median": int(statistics.median(times)),
        "elapsed_ms_p95": int(sorted(times)[min(len(times) - 1,
                                               int(len(times) * 0.95))]),
        "output_bytes_median": int(statistics.median(
            [s["output_bytes"] for s in samples] or [0])),
        "errors": [s["error"] for s in samples if s["error"]][:3],
    }


def render_markdown(p: dict) -> str:
    vol, det, conf = p["prompt"], p["determinism"], p["conformance"]
    rows = ["| 指标 | 编译前 | 编译后 | 变化 |", "|---|---|---|---|"]
    for k, label in (("chars", "字符"), ("words", "词"), ("lines", "行"),
                     ("bytes_utf8", "UTF-8 字节")):
        b, a = vol["before"][k], vol["after"][k]
        rows.append("| %s | %d | %d | %.0f%% |" % (
            label, b, a, (100 * (a - b) / max(b, 1))))
    lines = [
        "# 编译前后对照 · %s" % p["target"], "",
        "生成时间：%s" % p["generated_at"], "",
        "生成器：%s" % GENERATOR, "",
        "## 口径", "", p["scope_note"], "",
        "## Prompt 体积", "", *rows, "",
        "- tokenizer：`%s`；%s" % (
            p["prompt"]["tokenizer"]["method"],
            ("精确 token 数 %d → %d" % (p["prompt"]["tokenizer"]["before"],
                                     p["prompt"]["tokenizer"]["after"]))
            if p["prompt"]["tokenizer"]["before"] is not None
            else "未安装本地 tokenizer，故不给 token 数字（禁止用字符数冒充 token）"),
        "",
        "## 结构性收益（来自 IR，非模型实测）", "",
        "| 指标 | 值 |", "|---|---|",
        "| 可结晶步骤 | %d |" % p["procedure"]["code_steps"],
        "| 编译前工具往返（估算） | %d 次 |" % p["procedure"]["tool_calls_before"],
        "| 编译后工具往返 | %d 次 |" % p["procedure"]["tool_calls_after"],
        "",
        "## 运行器确定性", "",
        "| 指标 | 值 |", "|---|---|",
        "| 运行次数 | %d |" % det["requested_runs"],
        "| 成功次数 | %d |" % det["successful_runs"],
        "| 输出形状稳定 | %s |" % ("是" if det["shape_stable"] else "否"),
        "| 耗时中位数 | %d ms |" % det["elapsed_ms_median"],
        "| 耗时 P95 | %d ms |" % det["elapsed_ms_p95"],
        "| 输出字节中位数 | %d B |" % det["output_bytes_median"], "",
        "## 声明式用例符合度", "",
    ]
    if not conf["declared"]:
        lines += ["未声明 fixtures：本项 **NOT_DECLARED**，不产出遵循率数字。"
                  "在技能目录放 `bench-fixtures.json` 或用 `--fixtures` 指定。", ""]
    else:
        lines += ["| 指标 | 值 |", "|---|---|",
                  "| 用例数 | %d |" % conf["cases_total"],
                  "| 通过 | %d |" % conf["passed"],
                  "| 符合率 | %.0f%% |" % (100 * conf["passed"] / max(conf["cases_total"], 1)),
                  "", "| case | 结果 | 说明 |", "|---|---|---|"]
        for c in conf["cases"]:
            lines.append("| %s | %s | %s |" % (
                c["case_id"], "PASS" if c["passed"] else "FAIL",
                str(c["detail"]).replace("|", "\\|")[:120]))
        lines.append("")
    lines += [
        "## 对接带模型的 eval runner", "",
        "`%s` 每行一个 case，含 `prompt_before` / `prompt_after` 全文、场景输入、"
        "期望结构化输出与实际输出；外部 runner 可直接做 A/B 并回写结果。" % p["eval_cases"], "",
        "本工具不调用模型，因此**不报告遵循率、token 成本或答案质量**。", "",
        "产物在 `build/`，不进 `crystallize.lock.json`，`forge watch` 仍为 CLEAN。", "",
    ]
    return "\n".join(lines)


def run(d: Path, source_text: str, fixtures: dict = None, runs: int = 3,
        timeout: int = 60) -> dict:
    """Return the full benchmark payload. Raises on missing artifacts."""
    build = d / "build"
    runner = build / "scripts" / "crystallized.py"
    irp = build / "ir.final.json"
    minimalp = build / "SKILL.md"
    for p in (runner, irp, minimalp):
        if not p.is_file():
            raise FileNotFoundError("缺少编译产物：%s（先 `forge compile`）" % p)
    data = json.loads(irp.read_text(encoding="utf-8-sig"))
    minimal = minimalp.read_text(encoding="utf-8-sig")

    vb, va = volume(source_text), volume(minimal)
    tb, ta = token_count(source_text), token_count(minimal)
    det = _determinism(runner, d, runs)
    cases = [c for c in (fixtures or {}).get("cases", []) if isinstance(c, dict)]
    results = [_run_case(runner, d, c, timeout) for c in cases]
    declared = bool(cases)
    conf = {
        "declared": declared,
        "cases_total": len(results),
        "passed": sum(1 for c in results if c["passed"]),
        "cases": results,
    }
    code_steps = sum(1 for s in data.get("steps", []) if s.get("owner") == "code")
    return {
        "tool": "skill-forge bench", "version": VERSION,
        "generator": GENERATOR, "target": data.get("target", d.name),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope_note": SCOPE_NOTE,
        "prompt": {
            "before": vb, "after": va,
            "delta": {k: va[k] - vb[k] for k in vb},
            "tokenizer": {"method": tb["method"], "before": tb["count"],
                          "after": ta["count"] if ta["method"] == tb["method"] else None},
        },
        "procedure": {"code_steps": code_steps,
                      "tool_calls_before": code_steps, "tool_calls_after": 1},
        "determinism": det,
        "conformance": conf,
        "eval_cases": "build/bench-cases.jsonl",
        "fixtures_source": (fixtures or {}).get("_source", "none"),
        "passed": det["shape_stable"] and det["successful_runs"] == det["requested_runs"] \
            and (not declared or conf["passed"] == conf["cases_total"]),
    }


def eval_cases(payload: dict, d: Path, fixtures: dict, source_text: str,
               minimal: str) -> list:
    out = []
    for c in (fixtures or {}).get("cases", []):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id", "unnamed"))
        result = next((x for x in payload["conformance"]["cases"]
                       if x["case_id"] == cid), {})
        out.append({
            "case_id": payload["target"] + "/" + cid,
            "kind": "compiled-conformance",
            "passed": bool(result.get("passed")),
            "generator": GENERATOR,
            "prompt_before": source_text,
            "prompt_after": minimal,
            "inputs": {"files": sorted((c.get("files") or {}).keys()),
                       "remove": list(c.get("remove") or [])},
            "expected": c.get("expect", {}),
            "observed": result.get("observed", {}),
            "diffs": result.get("diffs", []),
            "note": str(c.get("note", "")),
            "model_eval": "NOT_RUN：本工具不调用模型；外部 runner 可据此做 A/B",
        })
    return out


def load_fixtures(d: Path, explicit: str = None) -> dict:
    p = Path(explicit).resolve() if explicit else d / "bench-fixtures.json"
    if not p.is_file():
        return {}
    obj = json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict) or not isinstance(obj.get("cases"), list):
        raise ValueError("fixtures 必须是含 cases 数组的对象：%s" % p)
    obj["_source"] = p.name
    return obj
