# CI 集成

`forge ci <root>` 把每个已编译 skill 的漂移与回归检查聚合成门禁。它**不更新基线、不写入任何技能目录、不需要第三方依赖**。

## 检查项

| case | 检查 | 失败条件 |
|---|---|---|
| `<skill>/watch` | 源 SKILL.md 摘要 + 4 个编译产物哈希 | 源文档被改、产物被手改、锁文件缺失或损坏 |
| `<skill>/verify` | 运行 `build/scripts/crystallized.py` 并比对 `fixtures/baseline.json` | 输出与基线不一致、STATUS=ERROR、无基线、退出码非 0 |
| `<skill>/review`（仅 `--strict`） | 是否存在未签发的机器草案 | 有 `ir.proposed.json` 但没有 `build/ir.json` |

发现范围与 `batch` 一致：递归找 `SKILL.md`，跳过 `build/`、`.git/`、`node_modules/` 等目录，因此不会把生成出来的 `build/SKILL.md` 当成新技能。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | GATE PASS，全部检查通过 |
| 1 | GATE FAIL，至少一项检查失败 |
| 2 | 目录不存在，或没有任何已编译 skill（加 `--allow-empty` 可放行） |

CI 里不要用 `--allow-empty`（会掩盖「编译产物被误删」），pre-commit hook 可以用，避免仓库早期还没有编译产物时阻塞提交。

## 输出格式

`--format jsonl` 每行一个 case，可直接喂给 Skill-Up / skills-evals / eval.json 快照：

```json
{"case_id": "notes-audit/watch", "passed": true, "check": "source+artifact drift", "detail": "CLEAN ...", "skill": "...", "generator": "skill-forge ci 1.2.0"}
{"case_id": "notes-audit/verify", "passed": true, "check": "regression baseline", "detail": "PASS ...", "outputs": {"STATUS": "NEEDS_CLEANUP", "MD_COUNT": "4"}, "diffs": [], "skill": "...", "generator": "skill-forge ci 1.2.0"}
```

`verify` case 会带上 `outputs` 与 `diffs`，便于在 CI 日志里直接定位是哪个键变了，而不用重跑脚本。

## 三种接法

**GitHub Actions** — 拷 `templates/ci/github-actions.yml` 到 `.github/workflows/skill-forge.yml`：

```yaml
- name: Drift + regression gate
  shell: bash
  run: |
    set -o pipefail
    python skill-forge/scripts/forge.py ci . --strict --format jsonl | tee forge-ci.jsonl
```

`set -o pipefail` 必须加，否则 `tee` 会吞掉门禁的非零退出码。

**GitLab CI** — 拷 `templates/ci/gitlab-ci.yml`，`python:3.11-slim` 即可，无需安装依赖。

**pre-commit** — 拷 `templates/ci/pre-commit.sh` 到 `.git/hooks/pre-commit` 并 `chmod +x`。用环境变量 `FORGE` 可覆盖脚本路径。

## 基线怎么管

1. 本地完成 `compile` 后，跑一次 `forge verify <dir> --update-baseline`，把 `build/fixtures/baseline.json` 提交进 Git。
2. CI 只比对，**绝不自动建基线**：缺基线直接判 `NO_BASELINE` FAIL，否则首次运行会「自动通过」，等于没有门禁。
3. 数据变更导致输出合法变化时，在本地显式 `--update-baseline` 并提交，让 diff 出现在 PR 里供人审。

## 常见失败与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| `DRIFT 源 SKILL.md 已变更` | 改了源文档没重新编译 | 重新 `propose → approve --yes → compile → verify --update-baseline`；**不要**在 CI 里自动重编译 |
| `DRIFT 生成产物被改动` | 有人手改了 `build/` 里的产物 | 撤销手改，改 IR 后重新编译 |
| `NO_BASELINE` | 基线没提交 | 本地建基线并提交 |
| `UNREVIEWED` | 只跑了 `propose` 没 `approve` | 人审后签发，或在合入前删掉草案 |
| `FAIL 与基线不一致` | 数据/脚本行为变了 | 看 jsonl 的 `diffs`，确认是有意变更还是回归 |

## 与批量报告配合

```bash
# 每周一次：只读体检，找出该硬化的技能
python scripts/forge.py batch . --out reports --name weekly --frequency freq.json
# 每次提交：门禁
python scripts/forge.py ci . --strict --format jsonl
```

`generator` 里的版本号随 `scripts/version.py` 自动变化，上面的 `1.2.0` 只是示例快照，不要在 CI 判定条件里硬编码它。

`batch` 是发现工具（可以失败、可以只跑本地），`ci` 是阻断工具（必须稳定、必须可复现）。两者共用同一套 `watch` / `verify` 语义，但 `batch` 的排序分数是启发式估算，**不要**把优先级分数写进 CI 判定条件。

## 上架前也跑一遍

`doctor` 与 `selftest` 不依赖具体 skill，适合放进同一个 workflow：

```yaml
- name: Skill package self-check
  run: |
    python skill-forge/scripts/forge.py doctor skill-forge
    python skill-forge/scripts/forge.py selftest skill-forge
- name: Drift + regression gate
  shell: bash
  run: |
    set -o pipefail
    python skill-forge/scripts/forge.py ci . --strict --format jsonl | tee forge-ci.jsonl
```

`selftest` 会在临时目录里跑完整编译链路，不触碰真实技能库，可作为发布前的回归网。

## 1.1.0：跨平台矩阵与干净目录验收

`templates/ci/github-actions.yml` 现在是 **3 OS × 2 Python 版本**矩阵：

```yaml
strategy:
  fail-fast: false
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
    python-version: ['3.9', '3.13']
```

每个 job 依次跑 `doctor` → `selftest` → **`smoke --no-zip`** → `ci --strict`。GitLab 模板
用 `parallel.matrix` 跑 3.9 / 3.13 双版本；pre-commit 保持轻量，只跑 `ci`（提交时跑
全量 smoke 太慢）。

### smoke 与 ci 的分工

| | `ci` | `smoke` |
|---|---|---|
| 对象 | 当前工作树里**已编译**的 skill | 一份**全新、无 build/** 的副本 |
| 作用 | 阻断漂移与回归 | 证明「这份拷贝能独立跑通」 |
| 写文件 | 从不写技能目录 | 只写临时目录，不碰源项目 |
| 负例 | 无 | 未放通 L3（run_script）、未放通插件、直接编译机器草案 |

`smoke` 默认优先解压 `dist/skill-forge-*.zip`（先校验 CRC），因此**发布前跑一次
`package` 再跑 `smoke`，等于用最终上传的那个包做验收**。仓库刚 checkout、还没有
`dist/` 时用 `--no-zip` 复制发行清单。命令：

```bash
python scripts/forge.py package .
python scripts/forge.py smoke .              # 用刚生成的 zip
python scripts/forge.py smoke . --no-zip     # CI checkout 场景
```

当前 smoke 共 32 步，覆盖四条路径、三条负例、`measure` / `report` / `bench` 与 `ci --strict`，
任一失败即退出 1 并打印失败命令与输出尾部。

### bench 的 JSONL 也能进 CI

`bench <dir> --format jsonl` 每行一个声明式用例，带 `passed` / `expected` / `observed` /
`diffs`。它测的是运行器在固定输入下的行为，**不是模型遵循率**。想把它接进流水线时，
只针对带 `bench-fixtures.json` 的 skill 跑，并把结果并入 `ci --format jsonl` 的输出；
不要用它替代 `verify`（基线）或 `watch`（漂移）。

`doctor` 另有一层 AST 可移植性门禁（encoding / 路径分隔符 / shell=True / timeout），
三平台矩阵会先跑它再跑 `selftest` 与 `smoke`。本地 Windows 实跑不等于 Linux/macOS 已验证。
