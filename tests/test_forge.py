"""End-to-end and regression tests for skill-forge.

Run:  python scripts/forge.py selftest -v
   or python -m unittest discover -s tests -t . -v

Every test creates its own temp directory. Nothing in the real skill library
is touched. Zero third-party dependencies.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ir as IR  # noqa: E402
import analyzer  # noqa: E402
import core  # noqa: E402
import emitter  # noqa: E402
import proposer  # noqa: E402
import stability  # noqa: E402
import verifier  # noqa: E402
import release  # noqa: E402
import bench  # noqa: E402
import oppack  # noqa: E402
import portability  # noqa: E402
from version import VERSION  # noqa: E402

SOURCE = """# demo

1. 统计 `data/*.md` 的总数
2. 统计含 TODO 的文件数
3. 超过 1 → NEEDS_CLEANUP，否则 OK
4. 总结结论给负责人
不要删除任何文件
"""


def make_skill(tmp: Path, text: str = SOURCE, name: str = "demo"):
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


def base_ir(text: str, steps=None, **over):
    ir = {
        "version": "1", "target": "demo", "source_sha256": IR.sha256_text(text),
        "review_status": "APPROVED", "approved_by": "tester",
        "allow_shell": False, "allow_run_script": False,
        "prohibitions": ["不删除文件"],
        "kept_for_model": ["总结结论"],
        "steps": steps if steps is not None else [
            {"id": "s1", "source": "SKILL.md:L3", "owner": "code",
             "op": "count_files", "args": {"glob": "notes/*.md"}, "emit": "md_count"},
            {"id": "s2", "source": "SKILL.md:L5", "owner": "code",
             "op": "decide", "args": {"rules": [{"when": "md_count > 1",
                                                 "status": "WARN"}], "else": "OK"},
             "emit": "status"},
            {"id": "s3", "source": "SKILL.md:L6", "owner": "model", "note": "总结"},
        ],
    }
    ir.update(over)
    # A correctly signed IR (as produced by `approve --enable-run-script`)
    # records who opened the L2/L3 switch. Tests that deliberately omit the
    # signature do so explicitly via over=.
    if (ir.get("allow_shell") or ir.get("allow_run_script")
            or ir.get("allow_plugins")) and "elevated_by" not in over:
        ir["elevated_by"] = ir.get("approved_by")
    return ir


class TestIRValidation(unittest.TestCase):
    def test_path_traversal_all_platforms(self):
        for bad in ("../x", "a/../../b", "/etc/passwd", "C:/Windows/x",
                    "C:\\Windows\\x", "\\\\server\\share", "a/./../b",
                    "https://x/y", "", "a\x00b"):
            self.assertTrue(IR.bad_path(bad), "should reject %r" % bad)
        for good in ("notes/a.md", "notes/**/*.md", ".", "scripts/run.py"):
            self.assertFalse(IR.bad_path(good), "should accept %r" % good)

    def test_bad_regex_rejected_at_compile_time(self):
        ir = base_ir(SOURCE, steps=[
            {"id": "s1", "source": "SKILL.md:L3", "owner": "code",
             "op": "count_matches", "args": {"glob": "*", "pattern": "([unclosed"},
             "emit": "hits"}])
        errors, _ = IR.validate(ir, SOURCE)
        self.assertTrue(any("正则" in e for e in errors), errors)

    def test_reserved_emit_rejected(self):
        ir = base_ir(SOURCE, steps=[
            {"id": "s1", "source": "SKILL.md:L3", "owner": "code",
             "op": "count_files", "args": {"glob": "*"}, "emit": "steps"}])
        errors, _ = IR.validate(ir, SOURCE)
        self.assertTrue(any("保留键" in e for e in errors), errors)

    def test_decide_variable_must_be_declared(self):
        ir = base_ir(SOURCE, steps=[
            {"id": "s1", "source": "SKILL.md:L3", "owner": "code",
             "op": "decide", "args": {"rules": [{"when": "ghost > 1",
                                                 "status": "WARN"}]},
             "emit": "status"}])
        errors, _ = IR.validate(ir, SOURCE)
        self.assertTrue(any("未在前序步骤声明" in e for e in errors), errors)

    def test_proposal_gate_double_lock(self):
        ir = base_ir(SOURCE, review_status="PROPOSED")
        ir["steps"][0]["proposal"] = {"confidence": "low"}
        errors, _ = IR.validate(ir, SOURCE)
        self.assertTrue(any("机器草案" in e for e in errors), errors)
        self.assertTrue(any("proposal 元数据" in e for e in errors), errors)

    def test_privileged_ops_need_explicit_switch_and_signer(self):
        st = [{"id": "s1", "source": "SKILL.md:L3", "owner": "code",
               "op": "run_script", "args": {"path": "scripts/x.py"}, "emit": "out"}]
        errors, _ = IR.validate(base_ir(SOURCE, steps=st), SOURCE)
        self.assertTrue(any("allow_run_script" in e for e in errors), errors)
        ir = base_ir(SOURCE, steps=st, allow_run_script=True, approved_by="")
        errors, _ = IR.validate(ir, SOURCE)
        self.assertTrue(any("approved_by" in e for e in errors), errors)

    def test_run_script_only_py_and_arg_types(self):
        st = [{"id": "s1", "source": "SKILL.md:L3", "owner": "code",
               "op": "run_script", "args": {"path": "x.sh"}, "emit": "out"}]
        errors, _ = IR.validate(base_ir(SOURCE, steps=st, allow_run_script=True), SOURCE)
        self.assertTrue(any(".py" in e for e in errors), errors)
        st2 = [{"id": "s1", "source": "SKILL.md:L3", "owner": "code",
                "op": "collect", "args": {"glob": "*", "limit": "many"}, "emit": "out"}]
        errors, _ = IR.validate(base_ir(SOURCE, steps=st2), SOURCE)
        self.assertTrue(any("整数" in e for e in errors), errors)

    def test_source_sha_mismatch_rejected(self):
        ir = base_ir(SOURCE, source_sha256="0" * 64)
        errors, _ = IR.validate(ir, SOURCE)
        self.assertTrue(any("已变更" in e for e in errors), errors)

    def test_condition_has_no_eval(self):
        with self.assertRaises(ValueError):
            IR.eval_condition("__import__('os').system('echo hi') == 0", {})
        self.assertTrue(IR.eval_condition("n > 2", {"n": 3}))
        self.assertFalse(IR.eval_condition("n > 2", {"n": 1}))
        self.assertTrue(IR.eval_condition("owner == 'wwd'", {"owner": "wwd"}))

    def test_valid_ir_passes(self):
        errors, warnings = IR.validate(base_ir(SOURCE), SOURCE)
        self.assertEqual(errors, [], errors)


class TestAnalyzer(unittest.TestCase):
    def test_frontmatter_only_at_file_start(self):
        lines = ["---", "name: x", "description: y", "---", "",
                 "1. 统计文件数", "---", "2. 统计含 TODO 的文件数"]
        self.assertEqual(analyzer.frontmatter_end(lines), 4)
        cands = analyzer.scan("\n".join(lines))
        self.assertTrue(any(c["line"] == 8 for c in cands), cands)

    def test_no_frontmatter_horizontal_rule_does_not_swallow(self):
        text = "# t\n\n1. 统计文件数\n\n---\n\n2. 统计含 TODO 的文件数\n"
        self.assertEqual(analyzer.frontmatter_end(text.splitlines()), 0)
        cands = analyzer.scan(text)
        self.assertTrue(any(c["line"] == 7 for c in cands), cands)

    def test_proposer_keeps_body_without_frontmatter(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t), "# t\n\n1. 统计文件数\n\n---\n\n"
                                   "2. 统计 `notes/*.md` 的总数\n"
                                   "3. 总结结论给负责人\n")
            draft = proposer.propose(d, (d / "SKILL.md").read_text(encoding="utf-8"))
            self.assertTrue(any("SKILL.md:L7" in s["source"] or "SKILL.md:L" in s["source"]
                                for s in draft["steps"]))
            self.assertTrue(draft["kept_for_model"])


class TestVerifier(unittest.TestCase):
    def test_missing_baseline_never_created_implicitly(self):
        with tempfile.TemporaryDirectory() as t:
            build = Path(t) / "build"
            (build / "scripts").mkdir(parents=True)
            runner = build / "scripts" / "crystallized.py"
            runner.write_text('print("=== RESULT ===")\nprint("STATUS=OK")\n'
                              'print("X=1")\nprint("=== META ===")\nprint("STEPS=0")\n',
                              encoding="utf-8")
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = verifier.verify(build, False, "text")
            self.assertEqual(rc, 1)
            self.assertIn("NO_BASELINE", buf.getvalue())
            self.assertFalse((build / "fixtures" / "baseline.json").exists())

    def test_baseline_then_regression_and_jsonl(self):
        with tempfile.TemporaryDirectory() as t:
            build = Path(t) / "build"
            (build / "scripts").mkdir(parents=True)
            (build / "scripts" / "crystallized.py").write_text(
                'print("STATUS=OK")\nprint("X=1")\n', encoding="utf-8")
            from contextlib import redirect_stdout
            import io
            b1 = io.StringIO()
            with redirect_stdout(b1):
                self.assertEqual(verifier.verify(build, True, "text"), 0)
            b2 = io.StringIO()
            with redirect_stdout(b2):
                self.assertEqual(verifier.verify(build, False, "jsonl"), 0)
            self.assertIn('"passed": true', b2.getvalue())
            (build / "scripts" / "crystallized.py").write_text(
                'print("STATUS=OK")\nprint("X=2")\n', encoding="utf-8")
            b3 = io.StringIO()
            with redirect_stdout(b3):
                self.assertEqual(verifier.verify(build, False, "jsonl"), 1)
            self.assertIn('"passed": false', b3.getvalue())


class TestCompilePipeline(unittest.TestCase):
    def _prepare(self, d: Path, ir_obj=None, extras=True):
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        if extras:
            (d / "notes").mkdir(exist_ok=True)
            for n in ("a.md", "b.md"):
                (d / "notes" / n).write_text("TODO: x\n", encoding="utf-8")
            (d / "meta.json").write_text('{"owner": "wwd"}', encoding="utf-8")
            (d / "rows.csv").write_text("k,v\na,1\nb,2\n", encoding="utf-8")
            (d / "readme.txt").write_text("alpha beta\n", encoding="utf-8")
            (d / "scripts").mkdir(exist_ok=True)
            (d / "scripts" / "helper.py").write_text(
                'import json\nprint(json.dumps({"ok": True, "n": 7}))\n', encoding="utf-8")
        ir_obj = ir_obj or base_ir(text)
        p = d / "build" / "ir.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(ir_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        return p, text

    def test_new_ops_run_end_to_end(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t), SOURCE + "\n" * 6)
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            n = len(text.splitlines())
            steps = [
                {"id": "s1", "source": "SKILL.md:L3", "owner": "code",
                 "op": "read_text", "args": {"path": "readme.txt"}, "emit": "txt"},
                {"id": "s2", "source": "SKILL.md:L3", "owner": "code",
                 "op": "parse_csv", "args": {"path": "rows.csv"}, "emit": "rows"},
                {"id": "s3", "source": "SKILL.md:L3", "owner": "code",
                 "op": "pluck", "args": {"from": "rows", "key": "k"}, "emit": "keys"},
                {"id": "s4", "source": "SKILL.md:L3", "owner": "code",
                 "op": "count_lines", "args": {"glob": "notes/*.md"}, "emit": "lines"},
                {"id": "s5", "source": "SKILL.md:L3", "owner": "code",
                 "op": "file_exists", "args": {"path": "meta.json"}, "emit": "has_meta"},
                {"id": "s6", "source": "SKILL.md:L3", "owner": "code",
                 "op": "regex_extract", "args": {"from": "txt", "pattern": "[a-z]+"},
                 "emit": "words"},
                {"id": "s7", "source": "SKILL.md:L3", "owner": "code",
                 "op": "run_script", "args": {"path": "scripts/helper.py"},
                 "emit": "helper"},
                {"id": "s8", "source": "SKILL.md:L5", "owner": "code",
                 "op": "decide", "args": {"rules": [{"when": "lines > 1",
                                                     "status": "WARN"}], "else": "OK"},
                 "emit": "status"},
            ]
            ir_obj = base_ir(text, steps=steps, allow_run_script=True)
            self._prepare(d, ir_obj)
            errors, _ = IR.validate(ir_obj, text)
            self.assertEqual(errors, [], errors)
            rc, lines = core.compile_skill(d)
            self.assertEqual(rc, 0, "\n".join(lines))
            proc = verifier.run(d / "build" / "scripts" / "crystallized.py")
            kv = verifier.parse_kv(proc.stdout)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(kv.get("STATUS"), "WARN", proc.stdout)
            self.assertEqual(kv.get("LINES"), "2")
            self.assertEqual(kv.get("HAS_META"), "true")
            self.assertEqual(kv.get("KEYS"), "a,b")
            self.assertEqual(kv.get("WORDS"), "alpha,beta")
            self.assertEqual(json.loads(kv["HELPER"]), {"ok": True, "n": 7})
            self.assertIn("v", kv["ROWS"])

    def test_run_script_rejects_escape_at_runtime(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t), SOURCE)
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            self._prepare(d, base_ir(text, steps=[
                {"id": "s1", "source": "SKILL.md:L3", "owner": "code",
                 "op": "run_script", "args": {"path": "../evil.py"}, "emit": "out"}],
                allow_run_script=True))
            errors, _ = IR.validate(base_ir(text), text)
            self.assertEqual(errors, [])
            # Gate catches the traversal before emission.
            rc, lines = core.compile_skill(d)
            self.assertEqual(rc, 2, "\n".join(lines))

    def test_backup_is_not_overwritten_on_recompile(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t))
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            # "auto" binds to the current source, so we can recompile after a
            # source edit and specifically exercise the backup-preservation rule.
            self._prepare(d, base_ir(text, source_sha256="auto"))
            self.assertEqual(core.compile_skill(d)[0], 0)
            bak = d / "build" / "backup" / "SKILL.md"
            first = bak.read_bytes()
            (d / "SKILL.md").write_text(text + "\n", encoding="utf-8")
            # force is needed: source drift is now guarded
            self.assertEqual(core.compile_skill(d, force=True)[0], 0)
            self.assertEqual(bak.read_bytes(), first, "backup must stay original")

    def test_drift_guard_blocks_recompile_without_force(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t))
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            self._prepare(d, base_ir(text, source_sha256="auto"))
            self.assertEqual(core.compile_skill(d)[0], 0)
            (d / "SKILL.md").write_text(text + "\n# changed\n", encoding="utf-8")
            rc, lines = core.compile_skill(d)
            self.assertEqual(rc, 2)
            self.assertTrue(any("锁文件不一致" in x for x in lines), lines)
            self.assertEqual(core.compile_skill(d, force=True)[0], 0)
            self.assertTrue((d / "build" / "override-audit.json").is_file())

    def test_minimal_skill_always_has_frontmatter(self):
        text = "# no frontmatter\n\n1. 统计文件数\n"
        minimal = emitter.render_minimal_skill(base_ir(text), text)
        fm = release._frontmatter(minimal)
        self.assertTrue(fm.get("name"))
        self.assertTrue(fm.get("description"))
        self.assertEqual(fm.get("version"), VERSION)

    def test_watch_drift_and_restore(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t))
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            self._prepare(d)
            self.assertEqual(core.compile_skill(d)[0], 0)
            self.assertEqual(core.watch_skill(d)[0], 0)
            (d / "SKILL.md").write_text(text + "\n", encoding="utf-8")
            self.assertEqual(core.watch_skill(d)[0], 1)
            (d / "SKILL.md").write_text(text, encoding="utf-8")
            self.assertEqual(core.watch_skill(d)[0], 0)
            (d / "build" / "report.md").write_text("tampered", encoding="utf-8")
            self.assertEqual(core.watch_skill(d)[0], 1)


class TestProposeApproveFill(unittest.TestCase):
    def _example(self, tmp: Path, name="notes-audit-auto"):
        src = ROOT / "examples" / name
        d = tmp / name
        shutil.copytree(src / "notes", d / "notes")
        shutil.copy2(src / "SKILL.md", d / "SKILL.md")
        return d

    def test_full_auto_path(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._example(Path(t))
            self.assertEqual(core.propose_skill(d)[0], 0)
            rc, lines = core.approve_skill(d, yes=False)
            self.assertEqual(rc, 1)
            patch = d / "patch.json"
            patch.write_text(json.dumps({"steps_add": [], "by": "tester"}), encoding="utf-8")
            self.assertEqual(core.fill_skill(d, patch, "tester")[0], 0)
            rc, lines = core.approve_skill(d, reviewer="wwd", yes=True)
            self.assertEqual(rc, 0, "\n".join(lines))
            rc, lines = core.compile_skill(d)
            self.assertEqual(rc, 0, "\n".join(lines))
            self.assertEqual(core.verify_skill(d, True)[0], 0)
            self.assertEqual(core.verify_skill(d, False, "jsonl")[0], 0)
            self.assertEqual(core.watch_skill(d)[0], 0)
            kv = verifier.parse_kv(
                verifier.run(d / "build" / "scripts" / "crystallized.py").stdout)
            self.assertEqual(kv.get("STATUS"), "NEEDS_CLEANUP", kv)
            self.assertEqual(kv.get("MD_COUNT"), "4", kv)

    def test_fill_rejects_unknown_op_without_writing(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._example(Path(t))
            core.propose_skill(d)
            before = (d / "build" / "ir.proposed.json").read_bytes()
            patch = d / "patch.json"
            patch.write_text(json.dumps({"steps_add": [
                {"id": "bad", "source": "SKILL.md:L10", "owner": "code",
                 "op": "rm_rf", "args": {}, "emit": "x"}]}), encoding="utf-8")
            rc, lines, _ = core.fill_skill(d, patch)
            self.assertEqual(rc, 2)
            self.assertTrue(any("白名单" in x for x in lines), lines)
            self.assertEqual((d / "build" / "ir.proposed.json").read_bytes(), before)

    def test_raw_proposal_cannot_compile(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._example(Path(t))
            core.propose_skill(d)
            rc, lines = core.compile_skill(d, d / "build" / "ir.proposed.json")
            self.assertEqual(rc, 2)
            self.assertTrue(any("机器草案" in x for x in lines), lines)


class TestStability(unittest.TestCase):
    def test_not_ready_and_force_override(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t))
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            (d / "notes").mkdir()
            (d / "notes" / "a.md").write_text("x", encoding="utf-8")
            p = d / "build" / "ir.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(base_ir(text, source_sha256="auto")), encoding="utf-8")
            # two different output shapes in the history
            stability.record(d, "STATUS=OK\nA=1\n", 1)
            stability.record(d, "STATUS=OK\nB=1\n", 1)
            stability.record(d, "STATUS=OK\nC=1\n", 1)
            status, reason, _ = stability.check(d, min_runs=3)
            self.assertEqual(status, stability.NOT_READY, reason)
            allowed, st2, _, _ = stability.guard(d, force=False)
            self.assertFalse(allowed)
            self.assertEqual(st2, stability.NOT_READY)
            allowed, st3, _, _ = stability.guard(d, force=True)
            self.assertTrue(allowed)
            self.assertEqual(st3, stability.OVERRIDDEN)
            self.assertTrue((d / "build" / "guard-override.json").is_file())

    def test_measure_reports_shape_stability(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t))
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            (d / "notes").mkdir()
            for n in ("a.md", "b.md"):
                (d / "notes" / n).write_text("x", encoding="utf-8")
            p = d / "build" / "ir.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(base_ir(text)), encoding="utf-8")
            self.assertEqual(core.compile_skill(d, skip_guard=True)[0], 0)
            m = stability.measure(d, 3, str(d), record_shapes=True)
            self.assertEqual(m["successful_runs"], 3)
            self.assertTrue(m["shape_stable"])
            self.assertGreater(m["output_bytes_median"], 0)
            self.assertGreaterEqual(len(stability._read_runs(d)), 3)


class TestBatchCI(unittest.TestCase):
    def test_batch_is_read_only_and_ci_jsonl(self):
        import batch as batchmod
        import ci as cimod
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = make_skill(root)
            (d / "notes").mkdir()
            (d / "notes" / "a.md").write_text("x", encoding="utf-8")
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            (d / "build").mkdir()
            (d / "build" / "ir.json").write_text(
                json.dumps(base_ir(text)), encoding="utf-8")
            before = sorted(str(p.relative_to(d)) for p in d.rglob("*"))
            rows = [batchmod.analyze_one(d, root, {}, [])]
            self.assertEqual(sorted(str(p.relative_to(d)) for p in d.rglob("*")), before)
            self.assertEqual(rows[0]["skill"], "demo")
            cases = cimod.collect(root, strict=False)
            self.assertEqual(cases, [])  # nothing compiled yet
            self.assertEqual(core.compile_skill(d, skip_guard=True)[0], 0)
            core.verify_skill(d, True)
            cases = cimod.collect(root, strict=False)
            self.assertEqual(len(cases), 2)
            self.assertTrue(all(c["passed"] for c in cases), cases)


class TestRelease(unittest.TestCase):
    def test_version_module_single_source(self):
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")

    def test_doctor_passes(self):
        rc, lines = release.doctor(ROOT)
        self.assertEqual(rc, 0, "\n".join(lines))

    def test_package_roundtrip(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t)
            rc, lines, zp = release.package(ROOT, out)
            self.assertEqual(rc, 0, "\n".join(lines))
            self.assertTrue(zp.is_file())
            import zipfile
            with zipfile.ZipFile(zp) as zf:
                self.assertIsNone(zf.testzip())
                self.assertIn("skill-forge/SKILL.md", zf.namelist())
                self.assertIn("skill-forge/MANIFEST.json", zf.namelist())
                self.assertFalse(any("/build/" in n for n in zf.namelist()))
                self.assertFalse(any(n.endswith(".pyc") for n in zf.namelist()))
            manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], VERSION)
            self.assertGreater(manifest["file_count"], 30)


class TestCLI(unittest.TestCase):
    def _run(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(SCRIPTS / "forge.py")] + list(args),
                              cwd=str(cwd or ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)

    def test_version_and_help(self):
        p = self._run("--version")
        self.assertEqual(p.returncode, 0)
        self.assertIn(VERSION, p.stdout)
        p = self._run("--help")
        self.assertEqual(p.returncode, 0)
        for cmd in ("propose", "approve", "compile", "verify", "watch", "batch",
                    "ci", "guard", "measure", "doctor", "package", "selftest",
                    "fill", "report", "smoke", "bench"):
            self.assertIn(cmd, p.stdout)

    def test_cli_invalid_dir_fails_cleanly(self):
        p = self._run("scan", str(ROOT / "__no_such_dir__"))
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("Traceback", p.stderr + p.stdout)

    def test_cli_compile_without_ir_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t))
            p = self._run("compile", str(d))
            self.assertEqual(p.returncode, 2)
            self.assertIn("缺少 IR", p.stdout)


class TestV11Regressions(unittest.TestCase):
    """Regressions for defects found while building the v1.1 data-audit example."""

    def _prepare(self, d, ir_obj):
        (d / "build").mkdir(parents=True, exist_ok=True)
        (d / "build" / "ir.json").write_text(
            json.dumps(ir_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def _skill(self, t, name="demo"):
        d = make_skill(Path(t), SOURCE, name)
        (d / "cfg.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
        (d / "scripts").mkdir(exist_ok=True)
        (d / "scripts" / "h.py").write_text(
            'print("{\\"ok\\": true}")\n', encoding="utf-8")
        return d

    def test_generated_runner_uses_python_not_json_literals(self):
        """default=False / missing default used to emit `false` / `null`."""
        with tempfile.TemporaryDirectory() as t:
            d = self._skill(t)
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            steps = [
                {"id": "s1", "source": "SKILL.md:L3", "owner": "code",
                 "op": "read_json", "args": {"path": "cfg.json"}, "emit": "cfg"},
                {"id": "s2", "source": "SKILL.md:L3", "owner": "code",
                 "op": "json_get", "args": {"from": "cfg", "key": "x",
                                            "default": False}, "emit": "flag"},
                {"id": "s3", "source": "SKILL.md:L3", "owner": "code",
                 "op": "json_get", "args": {"from": "cfg", "key": "y"},
                 "emit": "none_val"},
                {"id": "s4", "source": "SKILL.md:L5", "owner": "code",
                 "op": "decide", "args": {"rules": [{"when": "flag == false",
                                                     "status": "WARN"}],
                                         "else": "OK"}, "emit": "status"},
            ]
            self._prepare(d, base_ir(text, steps=steps))
            rc, lines = core.compile_skill(d)
            self.assertEqual(rc, 0, "\n".join(lines))
            proc = verifier.run(d / "build" / "scripts" / "crystallized.py")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            kv = verifier.parse_kv(proc.stdout)
            self.assertEqual(kv["STATUS"], "WARN")
            self.assertEqual(kv["FLAG"], "false")
            self.assertEqual(kv["NONE_VAL"], "")

    def test_bom_csv_header_is_clean(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t), SOURCE)
            (d / "m.csv").write_text("name,value\na,1\nb,2\n",
                                     encoding="utf-8-sig")
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            steps = [
                {"id": "s1", "source": "SKILL.md:L3", "owner": "code",
                 "op": "parse_csv", "args": {"path": "m.csv"}, "emit": "rows"},
                {"id": "s2", "source": "SKILL.md:L3", "owner": "code",
                 "op": "pluck", "args": {"from": "rows", "key": "name"},
                 "emit": "names"},
                {"id": "s3", "source": "SKILL.md:L5", "owner": "code",
                 "op": "decide", "args": {"rules": [{"when": "names > 1",
                                                     "status": "WARN"}],
                                         "else": "OK"}, "emit": "status"},
            ]
            self._prepare(d, base_ir(text, steps=steps))
            self.assertEqual(core.compile_skill(d)[0], 0)
            proc = verifier.run(d / "build" / "scripts" / "crystallized.py")
            kv = verifier.parse_kv(proc.stdout)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(kv["NAMES"], "a,b")
            self.assertNotIn("\ufeff", proc.stdout)

    def test_fill_rejects_privilege_escalation(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._skill(t)
            core.propose_skill(d)
            p = d / "escalate.json"
            p.write_text(json.dumps({"allow_run_script": True}), encoding="utf-8")
            rc, lines, _ = core.fill_skill(d, p)
            self.assertEqual(rc, 2)
            self.assertTrue(any("自我提权" in x for x in lines), lines)

    def test_fill_l3_step_then_explicit_human_grant(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._skill(t)
            core.propose_skill(d)
            p = d / "patch.json"
            p.write_text(json.dumps({
                "_hint": "documentation only",
                "steps_add": [{"id": "rx", "source": "SKILL.md:L3",
                               "owner": "code", "op": "run_script",
                               "args": {"path": "scripts/h.py", "timeout": 30},
                               "emit": "helper"}],
            }, ensure_ascii=False), encoding="utf-8")
            rc, lines, _ = core.fill_skill(d, p)
            self.assertEqual(rc, 0, "\n".join(lines))
            rc2, lines2 = core.approve_skill(d, yes=True, reviewer="tester")
            self.assertEqual(rc2, 2, "\n".join(lines2))
            self.assertTrue(any("enable-run-script" in x for x in lines2))
            rc3, lines3 = core.approve_skill(
                d, yes=True, reviewer="tester", enable_run_script=True)
            self.assertEqual(rc3, 0, "\n".join(lines3))
            approved = json.loads((d / "build" / "ir.json").read_text(encoding="utf-8"))
            self.assertTrue(approved["allow_run_script"])
            self.assertEqual(approved["elevated_by"], "tester")
            self.assertEqual(approved["approved_by"], "tester")
            rc4, lines4 = core.compile_skill(d)
            self.assertEqual(rc4, 0, "\n".join(lines4))

    def test_approve_rejects_unnecessary_grant(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._skill(t)
            core.propose_skill(d)
            rc, lines = core.approve_skill(
                d, yes=True, reviewer="tester", enable_run_script=True)
            self.assertEqual(rc, 2)
            self.assertTrue(any("无谓放通" in x for x in lines), lines)

    def test_elevated_by_must_match_approved_by(self):
        st = [{"id": "s1", "source": "SKILL.md:L3", "owner": "code",
               "op": "run_script", "args": {"path": "scripts/h.py"}, "emit": "out"}]
        good = base_ir(SOURCE, steps=st, allow_run_script=True)
        self.assertEqual(IR.validate(good, SOURCE)[0], [])
        no_sign = dict(good)
        no_sign.pop("elevated_by")
        self.assertTrue(any("elevated_by" in e for e in IR.validate(no_sign, SOURCE)[0]))
        mismatch = dict(good)
        mismatch["elevated_by"] = "someone-else"
        self.assertTrue(any("elevated_by" in e for e in IR.validate(mismatch, SOURCE)[0]))

    def test_dotted_directory_name_full_chain(self):
        self.assertEqual(IR.slugify_target("my.skill"), "my_skill")
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t), SOURCE, "my.skill")
            rc, _, draft = core.propose_skill(d)
            self.assertEqual(rc, 0)
            self.assertEqual(draft["target"], "my_skill")
            self.assertEqual(core.approve_skill(d, yes=True, reviewer="t")[0], 0)
            rc2, lines = core.compile_skill(d)
            self.assertEqual(rc2, 0, "\n".join(lines))
            self.assertEqual(core.watch_skill(d)[0], 0)

    def test_proposer_bans_and_suggestions_are_clean(self):
        text = (ROOT / "examples" / "data-audit" / "SKILL.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t), text, "data-audit")
            _, _, draft = core.propose_skill(d, write=False)
            bans = draft["prohibitions"]
            self.assertFalse(any(x.startswith("#") for x in bans), bans)
            self.assertFalse(any("需要判断" in x for x in bans), bans)
            self.assertTrue(any("不要联网" in x for x in bans), bans)
            for s in draft["proposal"]["suggestions"]:
                self.assertNotIn("allow_run_script", s["patch_hint"])
                self.assertIn("approve_hint", s)

    def test_guard_override_expires(self):
        from datetime import datetime, timedelta
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t), SOURCE)
            p = stability.override_file(d)
            p.parent.mkdir(parents=True, exist_ok=True)
            info = {"source_sha256": stability._source_sha(d), "reason": "test"}
            info["forced_at"] = datetime.now().isoformat(timespec="seconds")
            p.write_text(json.dumps(info), encoding="utf-8")
            self.assertTrue(stability.override_state(d)[0])
            old = datetime.now() - timedelta(hours=25)
            info["forced_at"] = old.isoformat(timespec="seconds")
            p.write_text(json.dumps(info), encoding="utf-8")
            self.assertFalse(stability.override_state(d)[0])
            info["forced_at"] = datetime.now().isoformat(timespec="seconds")
            info["source_sha256"] = "0" * 64
            p.write_text(json.dumps(info), encoding="utf-8")
            self.assertFalse(stability.override_state(d)[0])

    def test_measured_report_does_not_break_drift_lock(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._skill(t)
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            self._prepare(d, base_ir(text))
            self.assertEqual(core.compile_skill(d)[0], 0)
            measurement = {
                "measured_at": "2026-11-10 12:00:00", "requested_runs": 3,
                "successful_runs": 3, "shape_stable": True,
                "elapsed_ms_median": 12, "elapsed_ms_p95": 15,
                "output_bytes_median": 128, "scope_note": "test only",
            }
            (d / "build" / "measurement.json").write_text(
                json.dumps(measurement), encoding="utf-8")
            rc, lines = core.report_skill(d)
            self.assertEqual(rc, 0, "\n".join(lines))
            body = (d / "build" / "report.measured.md").read_text(encoding="utf-8")
            self.assertIn("实测数据", body)
            self.assertIn("12 ms", body)
            self.assertEqual(core.watch_skill(d)[0], 0)

    def test_data_audit_example_full_chain(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "data-audit"
            shutil.copytree(ROOT / "examples" / "data-audit", d,
                            ignore=shutil.ignore_patterns("build"))
            self.assertEqual(core.propose_skill(d)[0], 0)
            rc, lines, _ = core.fill_skill(d, d / "agent-patch.json")
            self.assertEqual(rc, 0, "\n".join(lines))
            rc, lines = core.approve_skill(
                d, yes=True, reviewer="tester", enable_run_script=True)
            self.assertEqual(rc, 0, "\n".join(lines))
            rc, lines = core.compile_skill(d)
            self.assertEqual(rc, 0, "\n".join(lines))
            proc = verifier.run(d / "build" / "scripts" / "crystallized.py")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            kv = verifier.parse_kv(proc.stdout)
            self.assertEqual(kv["STATUS"], "BLOCK")
            self.assertIn("latency_ms", kv["METRIC_NAMES"])
            self.assertEqual(kv["VALIDATION_OK"], "true")
            self.assertEqual(kv["VERSION"], "1.4.2")
            self.assertEqual(core.verify_skill(d, update=True)[0], 0)
            self.assertEqual(core.watch_skill(d)[0], 0)

    def test_smoke_materializes_a_complete_tree(self):
        import smoke
        with tempfile.TemporaryDirectory() as t:
            target, source = smoke._materialize(ROOT, Path(t), use_zip=False)
            self.assertTrue((target / "SKILL.md").is_file(), source)
            self.assertTrue((target / "scripts" / "smoke.py").is_file())
            self.assertTrue((target / "examples" / "data-audit" /
                             "agent-patch.json").is_file())
            self.assertGreaterEqual(len(smoke._steps(target)), 20)


class TestV12BenchPlugins(unittest.TestCase):
    """M3: offline benchmark, declared op plugins, portability gate."""

    def _data_audit(self, t):
        d = Path(t) / "data-audit"
        shutil.copytree(ROOT / "examples" / "data-audit", d,
                        ignore=shutil.ignore_patterns("build"))
        self.assertEqual(core.propose_skill(d)[0], 0)
        rc, lines, _ = core.fill_skill(d, d / "agent-patch.json")
        self.assertEqual(rc, 0, "\n".join(lines))
        rc, lines = core.approve_skill(d, yes=True, reviewer="tester",
                                       enable_run_script=True)
        self.assertEqual(rc, 0, "\n".join(lines))
        rc, lines = core.compile_skill(d)
        self.assertEqual(rc, 0, "\n".join(lines))
        return d

    def _plugin_audit(self, t):
        d = Path(t) / "plugin-audit"
        shutil.copytree(ROOT / "examples" / "plugin-audit", d,
                        ignore=shutil.ignore_patterns("build"))
        self.assertEqual(core.propose_skill(d)[0], 0)
        draft = json.loads((d / "build" / "ir.proposed.json").read_text(encoding="utf-8"))
        self.assertEqual(draft["plugin_ops"], ["risk_score"])
        self.assertFalse(draft["allow_plugins"])
        rc, lines, _ = core.fill_skill(d, d / "agent-patch.json")
        self.assertEqual(rc, 0, "\n".join(lines))
        return d

    def test_bench_volume_and_tokenizer_honesty(self):
        v = bench.volume("a b\n中文")
        self.assertEqual(v["chars"], 6)
        self.assertEqual(v["lines"], 2)
        tok = bench.token_count("a b")
        self.assertIn("method", tok)
        self.assertTrue(tok["count"] is None or isinstance(tok["count"], int))
        self.assertIn("不测量", bench.SCOPE_NOTE)

    def test_bench_fixtures_four_cases_and_no_drift(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._data_audit(t)
            fixtures = bench.load_fixtures(d)
            p = bench.run(d, core.read_skill(d), fixtures, runs=2)
            self.assertTrue(p["passed"])
            self.assertEqual(p["conformance"]["cases_total"], 4)
            self.assertEqual(p["conformance"]["passed"], 4)
            self.assertTrue(p["determinism"]["shape_stable"])
            self.assertTrue(p["prompt"]["tokenizer"]["method"].startswith(
                "tiktoken") or p["prompt"]["tokenizer"]["before"] is None)
            (d / "build" / "benchmark.json").write_text(
                json.dumps(p, ensure_ascii=False), encoding="utf-8")
            (d / "build" / "benchmark.md").write_text(
                bench.render_markdown(p), encoding="utf-8")
            self.assertEqual(core.watch_skill(d)[0], 0)

    def test_bench_rejects_bad_expectation_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._data_audit(t)
            bad = {"cases": [
                {"id": "wrong", "expect": {"STATUS": "OK"}},
                {"id": "escape", "files": {"../../evil": "x"},
                 "expect": {"STATUS": "OK"}},
            ]}
            p = bench.run(d, core.read_skill(d), bad, runs=1)
            self.assertFalse(p["passed"])
            self.assertEqual(p["conformance"]["passed"], 0)
            self.assertIn("路径越界", p["conformance"]["cases"][1]["detail"])

    def test_bench_without_fixtures_reports_not_declared(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._data_audit(t)
            p = bench.run(d, core.read_skill(d), {}, runs=1)
            self.assertFalse(p["conformance"]["declared"])
            self.assertEqual(p["conformance"]["cases_total"], 0)
            self.assertIn("NOT_DECLARED", bench.render_markdown(p))

    def test_plugin_discovery_never_imports_source(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t))
            (d / "plugins").mkdir()
            (d / "plugins" / "hostile.py").write_text(
                'raise RuntimeError("should never be imported")\n'
                'SPEC = {"name": "x", "args": []}\n'
                'def run(ctx, **kwargs): return 1\n', encoding="utf-8")
            plugins, errors = oppack.discover(d)
            self.assertEqual(errors, [])
            self.assertEqual(plugins[0]["name"], "x")

    def test_plugin_discovery_rejects_bad_spec(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t))
            (d / "plugins").mkdir()
            (d / "plugins" / "bad.py").write_text(
                'SPEC = {"name": "Bad-Name", "args": []}\n'
                'def run(ctx, **kwargs): return 1\n', encoding="utf-8")
            _, errors = oppack.discover(d)
            self.assertTrue(errors)
            self.assertIn("SPEC.name", errors[0])

    def test_plugin_restricted_namespace_blocks_os_and_disabled_switch(self):
        evil = ('SPEC = {"name": "evil", "args": []}\n'
                'def run(ctx, **kwargs):\n    import os\n    return 1\n')
        ns = {"__name__": "__main__"}
        exec(compile("ALLOW_PLUGINS = True\n" + oppack.plugin_block(
            [{"name": "evil", "source": evil}]), "probe", "exec"), ns)
        with self.assertRaises(ImportError):
            ns["op_plugin"]({}, "evil")
        safe = ('SPEC = {"name": "safe", "args": []}\n'
                'def run(ctx, **kwargs):\n    return 1\n')
        ns2 = {"__name__": "__main__"}
        exec(compile("ALLOW_PLUGINS = False\n" + oppack.plugin_block(
            [{"name": "safe", "source": safe}]), "probe2", "exec"), ns2)
        with self.assertRaises(ValueError):
            ns2["op_plugin"]({}, "safe")

    def test_plugin_ir_gates(self):
        text = SOURCE
        step = {"id": "s1", "source": "SKILL.md:L3", "owner": "code",
                "op": "plugin_risk_score", "args": {"weight": 2},
                "emit": "risk_score"}
        ir_obj = base_ir(text, steps=[step], allow_plugins=False,
                         plugin_ops=["risk_score"],
                         plugins_sha256="0" * 64)
        errors, _ = IR.validate(ir_obj, text)
        self.assertTrue(any("allow_plugins" in e for e in errors), errors)

        ir_obj["allow_plugins"] = True
        ir_obj.pop("elevated_by", None)
        errors, _ = IR.validate(ir_obj, text)
        self.assertTrue(any("elevated_by" in e for e in errors), errors)

        ir_obj["elevated_by"] = "someone-else"
        errors, _ = IR.validate(ir_obj, text)
        self.assertTrue(any("elevated_by" in e for e in errors), errors)

        ir_obj["elevated_by"] = "tester"
        ir_obj["plugin_ops"] = []
        errors, _ = IR.validate(ir_obj, text)
        self.assertTrue(any("plugin_ops" in e for e in errors), errors)

    def test_patch_cannot_self_enable_plugins(self):
        with tempfile.TemporaryDirectory() as t:
            d = make_skill(Path(t))
            self.assertEqual(core.propose_skill(d)[0], 0)
            p = d / "patch.json"
            p.write_text(json.dumps({"allow_plugins": True, "by": "agent"}),
                         encoding="utf-8")
            rc, lines, _ = core.fill_skill(d, p)
            self.assertEqual(rc, 2)
            self.assertTrue(any("不得自我提权" in x for x in lines))

    def test_plugin_example_full_chain_and_gates(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._plugin_audit(t)
            rc, lines = core.approve_skill(d, yes=True, reviewer="tester")
            self.assertEqual(rc, 2)
            self.assertTrue(any("--enable-plugins" in x for x in lines))
            rc, lines = core.approve_skill(d, yes=True, reviewer="tester",
                                           enable_plugins=True)
            self.assertEqual(rc, 0, "\n".join(lines))
            rc, lines = core.compile_skill(d)
            self.assertEqual(rc, 0, "\n".join(lines))
            proc = verifier.run(d / "build" / "scripts" / "crystallized.py")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            kv = verifier.parse_kv(proc.stdout)
            self.assertEqual(kv["STATUS"], "WARN")
            self.assertEqual(kv["RISK_SCORE"], "4")
            self.assertEqual(core.verify_skill(d, update=True)[0], 0)
            self.assertEqual(core.watch_skill(d)[0], 0)

            fixtures = bench.load_fixtures(d)
            p = bench.run(d, core.read_skill(d), fixtures, runs=2)
            self.assertTrue(p["passed"])
            self.assertEqual(p["conformance"]["passed"], 2)

    def test_plugin_hash_drift_blocks_watch_and_compile(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._plugin_audit(t)
            self.assertEqual(core.approve_skill(
                d, yes=True, reviewer="tester", enable_plugins=True)[0], 0)
            self.assertEqual(core.compile_skill(d)[0], 0)
            plugin = d / "plugins" / "risk_score.py"
            # Restore bytes, not text: on Windows write_text() would translate
            # LF to CRLF and legitimately trip the hash-lock again.
            original = plugin.read_bytes()
            plugin.write_bytes(original + b"\n# tampered\n")
            rc, msg = core.watch_skill(d)
            self.assertEqual(rc, 1)
            self.assertIn("plugins/", msg)
            rc, lines = core.compile_skill(d, force=True)
            self.assertEqual(rc, 2)
            self.assertTrue(any("sha256 不匹配" in x for x in lines))
            plugin.write_bytes(original)
            self.assertEqual(core.watch_skill(d)[0], 0)

    def test_portability_scan_is_clean_and_detects_bad_code(self):
        self.assertEqual(portability.scan(ROOT), {})
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "bad.py"
            p.write_text(
                "import subprocess\n"
                "x = open('a.txt')\n"
                "y = 'dir\\\\file.txt'\n"
                "subprocess.run(['ls'], shell=True)\n", encoding="utf-8")
            issues = portability.check_file(p)
            joined = " ".join(issues)
            self.assertIn("encoding", joined)
            self.assertIn("shell=True", joined)
            self.assertIn("反斜杠路径", joined)

    def test_runner_survives_unicode_path_and_non_utf8_locale(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "空 格 skill"
            d.mkdir()
            (d / "SKILL.md").write_text(SOURCE, encoding="utf-8")
            (d / "notes").mkdir()
            for n in ("a.md", "中文.md"):
                (d / "notes" / n).write_text("TODO\n", encoding="utf-8")
            ir_obj = base_ir(SOURCE)
            ir_obj["target"] = IR.slugify_target(d.name)
            (d / "build").mkdir()
            (d / "build" / "ir.json").write_text(
                json.dumps(ir_obj, ensure_ascii=False), encoding="utf-8")
            rc, lines = core.compile_skill(d)
            self.assertEqual(rc, 0, "\n".join(lines))
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "cp1252"
            env["PYTHONUTF8"] = "0"
            proc = subprocess.run(
                [sys.executable, str(d / "build" / "scripts" / "crystallized.py"),
                 "--root", str(d)], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60, env=env)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("STATUS=", proc.stdout)
            kv = verifier.parse_kv(proc.stdout)
            self.assertEqual(kv["STATUS"], "WARN")

    def test_smoke_covers_bench_and_plugin_paths(self):
        import smoke
        with tempfile.TemporaryDirectory() as t:
            target, _ = smoke._materialize(ROOT, Path(t), use_zip=False)
            steps = smoke._steps(target)
            flat = [s[1][0] for s in steps]
            self.assertIn("bench", flat)
            self.assertTrue(any("--enable-plugins" in s[1] for s in steps))
            self.assertGreaterEqual(len(steps), 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
