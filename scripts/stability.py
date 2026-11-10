"""NOT_READY guard and measured (not estimated) runner statistics.

The guard answers: "is this procedure stable enough to freeze into code?"
Freezing a procedure that is still changing every day is worse than not
optimising at all, so `compile` consults this module before emitting anything.

Evidence used (all local, no network, no model):
  1. Shape stability of the last N recorded runner outputs (key set).
  2. Churn of the source SKILL.md in git over the last 7 days.
  3. Presence of a signed, unexpired guard override (--force, audited).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import verifier
from version import VERSION

READY = "READY"
UNSAMPLED = "UNSAMPLED"
NOT_READY = "NOT_READY"
OVERRIDDEN = "OVERRIDDEN"

# A --force exemption is a *temporary*, source-bound decision, not a permanent
# licence. Without a TTL an old override kept bypassing NOT_READY forever.
OVERRIDE_TTL_SECONDS = 24 * 3600


def runs_file(d: Path) -> Path:
    return d / "build" / "fixtures" / "runs.jsonl"


def override_file(d: Path) -> Path:
    return d / "build" / "guard-override.json"


def record(d: Path, proc_stdout: str, elapsed_ms: int = 0) -> dict:
    """Append one observed runner shape. Never raises on an empty result."""
    kv = verifier.parse_kv(proc_stdout)
    keys = sorted(k for k in kv if k not in verifier.META_KEYS)
    digest = hashlib.sha256(json.dumps(
        {k: kv[k] for k in keys}, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")).hexdigest()[:16]
    row = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "keys": keys,
        "values_sha256_16": digest,
        "status": kv.get("STATUS", ""),
        "elapsed_ms": int(elapsed_ms),
    }
    p = runs_file(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _read_runs(d: Path):
    p = runs_file(d)
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def git_churn(d: Path, days: int = 7):
    """Return (commit_count, available). Failures are silently ignored."""
    try:
        proc = subprocess.run(
            ["git", "log", "--since=%d.days" % days, "--format=%H", "--", "SKILL.md"],
            cwd=str(d), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return 0, False
    if proc.returncode != 0:
        return 0, False
    return len([x for x in proc.stdout.splitlines() if x.strip()]), True


def check(d: Path, min_runs: int = 3, window: int = 5, churn_limit: int = 4):
    """Return (status, reason, detail-dict)."""
    rows = _read_runs(d)[-window:]
    commits, git_ok = git_churn(d)
    detail = {
        "samples": len(rows), "min_runs": min_runs, "window": window,
        "git_available": git_ok, "commits_7d": commits, "churn_limit": churn_limit,
    }
    if commits > churn_limit:
        return NOT_READY, (
            "源 SKILL.md 近 7 天有 %d 次提交（阈值 %d）：流程形状未稳定，先别结晶"
            % (commits, churn_limit)), detail
    if len(rows) >= min_runs:
        shapes = {tuple(r.get("keys", [])) for r in rows}
        if len(shapes) > 1:
            detail["distinct_shapes"] = [list(s) for s in shapes]
            return NOT_READY, (
                "最近 %d 次运行出现 %d 种不同的输出键集合：流程形状不稳定"
                % (len(rows), len(shapes))), detail
        detail["keys"] = rows[-1].get("keys", [])
    if len(rows) < min_runs:
        return UNSAMPLED, (
            "运行样本 %d 条 < 最小 %d 条：允许编译，但建议先 `forge measure` 采样"
            % (len(rows), min_runs)), detail
    return READY, "输出形状稳定（%d 次采样一致）" % len(rows), detail


def override_state(d: Path):
    """Return (valid, info_or_None, human_reason) for a guard exemption."""
    ov = override_file(d)
    if not ov.is_file():
        return False, None, ""
    try:
        info = json.loads(ov.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return False, None, "豁免文件损坏，已忽略"
    if info.get("source_sha256") != _source_sha(d):
        return False, info, "豁免绑定的是旧版源文档，已失效"
    try:
        age = (datetime.now() - datetime.fromisoformat(info["forced_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return False, info, "豁免时间不可解析，已失效"
    if age < 0:
        return False, info, "豁免时间在未来，已失效"
    if age > OVERRIDE_TTL_SECONDS:
        return False, info, "豁免已超过 24 小时，需重新 --force"
    return True, info, "豁免有效（剩余约 %d 分钟）" % (
        (OVERRIDE_TTL_SECONDS - age) // 60)


def guard(d: Path, force: bool = False, min_runs: int = 3):
    """Compile-time guard. Returns (allowed, status, reason, detail)."""
    status, reason, detail = check(d, min_runs=min_runs)
    valid, _, oreason = override_state(d)
    if valid:
        return True, OVERRIDDEN, "已有针对当前源文档的人工豁免（%s）：%s" % (
            oreason, reason), detail
    if status == NOT_READY and force:
        ov = override_file(d)
        ov.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        ov.write_text(json.dumps({
            "forced_at": now.isoformat(timespec="seconds"),
            "expires_at": datetime.fromtimestamp(
                now.timestamp() + OVERRIDE_TTL_SECONDS).isoformat(timespec="seconds"),
            "ttl_seconds": OVERRIDE_TTL_SECONDS,
            "generator": "skill-forge %s" % VERSION,
            "source_sha256": _source_sha(d),
            "reason": reason, "detail": detail,
            "warning": "人工强制编译：已冻结一个被判定为不稳定的流程；豁免 24 小时后自动失效",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True, OVERRIDDEN, "人工 --force 豁免已记录（24h 有效）：%s" % reason, detail
    return status != NOT_READY, status, reason, detail


def _source_sha(d: Path) -> str:
    from ir import sha256_text
    return sha256_text((d / "SKILL.md").read_text(encoding="utf-8"))


def measure(d: Path, runs: int = 5, root: str = "", record_shapes: bool = False):
    """Run the compiled runner N times and report measured statistics.

    Honest scope: this measures wall time, output bytes and output-shape
    consistency. It does NOT measure real tokenizer tokens or model adherence
    (those require an external eval runner with a real model).
    """
    runner = d / "build" / "scripts" / "crystallized.py"
    if not runner.is_file():
        return None
    samples = []
    import time
    for i in range(runs):
        t0 = time.time()
        try:
            proc = verifier.run(runner, root or None)
            ok = proc.returncode == 0
            out = proc.stdout
            err = proc.stderr
        except Exception as exc:  # pragma: no cover - defensive
            ok, out, err = False, "", str(exc)
        elapsed = int((time.time() - t0) * 1000)
        kv = verifier.parse_kv(out)
        keys = sorted(k for k in kv if k not in verifier.META_KEYS)
        samples.append({
            "i": i + 1, "ok": ok, "elapsed_ms": elapsed,
            "output_bytes": len(out.encode("utf-8")),
            "keys": keys, "status": kv.get("STATUS", ""),
            "error": err.strip()[:200] if not ok else "",
        })
        if record_shapes and ok:
            record(d, out, elapsed)
    ok_runs = [s for s in samples if s["ok"] and s["status"] != "ERROR"]
    shapes = {tuple(s["keys"]) for s in samples}
    times = sorted(s["elapsed_ms"] for s in ok_runs) or [0]
    bytes_ = [s["output_bytes"] for s in ok_runs] or [0]
    payload = {
        "generator": "skill-forge measure %s" % VERSION,
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "target": d.name, "requested_runs": runs,
        "successful_runs": len(ok_runs),
        "shape_stable": len(shapes) <= 1,
        "distinct_shapes": [list(s) for s in shapes],
        "elapsed_ms_median": times[len(times) // 2],
        "elapsed_ms_p95": times[min(len(times) - 1, int(len(times) * 0.95))],
        "output_bytes_median": sorted(bytes_)[len(bytes_) // 2],
        "samples": samples,
        "scope_note": ("实测范围：墙钟时间 / 输出字节 / 输出形状一致性。"
                       "不含真实 tokenizer token 数与模型步骤遵循率，需外部 eval runner。"),
    }
    return payload


def render_measurement(p: dict) -> str:
    return "\n".join([
        "# 结晶实测报告 · %s" % p["target"], "",
        "- 生成时间：%s" % p["measured_at"],
        "- 生成器：%s" % p["generator"], "",
        "## 汇总", "", "| 指标 | 实测值 |", "|---|---|",
        "| 请求运行次数 | %d |" % p["requested_runs"],
        "| 成功次数 | %d |" % p["successful_runs"],
        "| 输出形状稳定 | %s |" % ("是" if p["shape_stable"] else "否"),
        "| 墙钟时间中位数 | %d ms |" % p["elapsed_ms_median"],
        "| 墙钟时间 P95 | %d ms |" % p["elapsed_ms_p95"],
        "| 输出字节中位数 | %d B |" % p["output_bytes_median"], "",
        "## 逐次", "", "| # | 成功 | STATUS | 耗时(ms) | 输出(B) | 键数 |",
        "|---|---|---|---|---|---|",
        *["| %d | %s | %s | %d | %d | %d |" % (
            s["i"], "✓" if s["ok"] else "✗", s["status"], s["elapsed_ms"],
            s["output_bytes"], len(s["keys"])) for s in p["samples"]], "",
        "## 口径", "", p["scope_note"], "",
        "若要测真实 token 与步骤遵循率，把 `forge verify --format jsonl` 的输出喂给 "
        "Skill-Up / skills-evals / evals.json 等带模型的 eval runner。", "",
    ]) + "\n"
