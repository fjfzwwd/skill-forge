"""One-command clean-room acceptance test.

"Complete and runnable" is only meaningful if it holds in a pristine copy:
no working tree, no prebuilt build/ artifacts, no installed dependencies.
`forge smoke` materialises such a copy (preferring the release zip), then runs
the whole pipeline from scratch, including three negative gates.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from version import VERSION


def _materialize(root: Path, work: Path, use_zip: bool):
    if use_zip:
        zips = sorted((root / "dist").glob("skill-forge-*.zip"))
        if zips:
            z = zips[-1]
            with zipfile.ZipFile(z) as zf:
                bad = zf.testzip()
                if bad:
                    raise RuntimeError("发行包 CRC 损坏：%s (%s)" % (z.name, bad))
                zf.extractall(work)
            target = work / "skill-forge"
            if not (target / "SKILL.md").is_file():
                raise RuntimeError("发行包缺少顶层 skill-forge/SKILL.md")
            return target, "zip %s" % z.name
    import release
    target = work / "skill-forge"
    n = 0
    for rel in release.release_files(root):
        src, dst = root / rel, target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    return target, "tree copy (%d files)" % n


def _steps(target: Path):
    a = (target / "examples" / "notes-audit").as_posix()
    b = (target / "examples" / "notes-audit-auto").as_posix()
    c = (target / "examples" / "data-audit").as_posix()
    e = (target / "examples" / "plugin-audit").as_posix()
    root = target.as_posix()
    return [
        ("--version", ["--version"], 0),
        ("doctor", ["doctor", root], 0),
        ("selftest", ["selftest", root], 0),
        # Path B: hand-written, human-reviewed IR (M0 compatibility).
        ("notes-audit / compile", ["compile", a, "--ir", a + "/ir.json"], 0),
        ("notes-audit / baseline", ["verify", a, "--update-baseline"], 0),
        ("notes-audit / verify jsonl", ["verify", a, "--format", "jsonl"], 0),
        ("notes-audit / watch", ["watch", a], 0),
        # Path A: machine proposal -> explicit human approval.
        ("auto / propose", ["propose", b], 0),
        ("auto / approve", ["approve", b, "--yes", "--by", "smoke"], 0),
        ("auto / compile", ["compile", b], 0),
        ("auto / baseline", ["verify", b, "--update-baseline"], 0),
        ("auto / watch", ["watch", b], 0),
        # Path C: Agent patch + L3 human opt-in + measurements.
        ("data / propose", ["propose", c], 0),
        ("data / fill", ["fill", c, "--patch", c + "/agent-patch.json",
                         "--by", "smoke"], 0),
        ("data / NEG no L3 grant", ["approve", c, "--yes", "--by", "smoke"], 2),
        ("data / approve L3", ["approve", c, "--yes", "--by", "smoke",
                               "--enable-run-script"], 0),
        ("data / compile", ["compile", c], 0),
        ("data / baseline", ["verify", c, "--update-baseline"], 0),
        ("data / measure", ["measure", c, "--runs", "3"], 0),
        ("data / report", ["report", c], 0),
        ("data / bench", ["bench", c, "--runs", "2"], 0),
        ("data / watch", ["watch", c], 0),
        ("data / NEG raw proposal", ["compile", c, "--ir",
                                     c + "/build/ir.proposed.json"], 2),
        # Path D: declared op plugin, hash pinning, explicit L3 human grant.
        ("plugin / propose", ["propose", e], 0),
        ("plugin / fill", ["fill", e, "--patch", e + "/agent-patch.json",
                           "--by", "smoke"], 0),
        ("plugin / NEG no grant", ["approve", e, "--yes", "--by", "smoke"], 2),
        ("plugin / approve L3", ["approve", e, "--yes", "--by", "smoke",
                                 "--enable-plugins"], 0),
        ("plugin / compile", ["compile", e], 0),
        ("plugin / baseline", ["verify", e, "--update-baseline"], 0),
        ("plugin / bench", ["bench", e, "--runs", "2"], 0),
        ("plugin / watch", ["watch", e], 0),
        ("ci --strict", ["ci", root, "--strict"], 0),
    ]


def run(root: Path, use_zip: bool = True, timeout: int = 180):
    """Return (exit_code, lines). Never writes into `root`."""
    lines = []
    with tempfile.TemporaryDirectory(prefix="sf-smoke-") as tmp:
        work = Path(tmp)
        try:
            target, source = _materialize(root, work, use_zip)
        except Exception as exc:
            return 2, ["SMOKE SETUP FAIL：%s" % exc]
        forge = target / "scripts" / "forge.py"
        if not forge.is_file():
            return 2, lines + ["SMOKE SETUP FAIL：副本中缺少 scripts/forge.py"]
        lines.append("干净副本：%s" % source)
        lines.append("版本：skill-forge %s · Python %s" % (
            VERSION, sys.version.split()[0]))
        lines.append("")
        steps = _steps(target)
        t0 = time.time()
        for i, (label, args, expect) in enumerate(steps, 1):
            try:
                proc = subprocess.run(
                    [sys.executable, str(forge)] + args,
                    cwd=str(target / "scripts"), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout)
                rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
            except subprocess.TimeoutExpired:
                return 2, lines + ["[FAIL] %s 超时（%ds）" % (label, timeout)]
            if rc != expect:
                lines.append("[FAIL] %-30s rc=%d，预期 %d（第 %d/%d 步）" % (
                    label, rc, expect, i, len(steps)))
                lines.append("  cmd: forge " + " ".join(args))
                lines += ["  | " + x for x in out.strip().splitlines()[-14:]]
                return 1, lines
            lines.append("[PASS] %-30s rc=%d" % (label, rc))
        lines += ["", "SMOKE PASS：%d/%d 步通过，耗时 %.1fs（%s）" % (
            len(steps), len(steps), time.time() - t0, source)]
        return 0, lines
