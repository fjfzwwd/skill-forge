# skill-forge · v1.2.0

把 SKILL.md 里的确定性步骤编译成自包含脚本，让模型只做判断。**零第三方依赖**，Python 3.9+，Windows / macOS / Linux 均可跑。

1.2.0 在 1.1.0 的 15 个内置 op、Agent 语义补丁、NOT_READY 护栏、实测报告、一键验收之上，补齐 **M3：`forge bench` 编译前后离线对照 + eval 适配器、声明式 op 插件（哈希锁定 / 受限命名空间 / L3 人审放通）、可移植性静态门禁**。

设计文档见 `PRD.md`，规则见 `references/`，变更记录见 `CHANGELOG.md`。

## 安装

把本目录放进技能目录即可（如 `~/.workbuddy/skills/skill-forge/`）。无需 pip install。

```bash
python scripts/forge.py --version
python scripts/forge.py doctor .         # 上架自检
python scripts/forge.py selftest . -v    # 59 条内置回归
python scripts/forge.py smoke .          # 干净目录 32 步端到端验收（推荐）
```

`smoke` 会把发行包解压到全新临时目录、从零跑完所有示例与 CI 门禁，是判断「这份拷贝是否真的能独立运行」最快的方式。

## 四条路径

| 路径 | 适用 | 流程 |
|---|---|---|
| **A · 自动草案** | 中文编号步骤 + 统计/收集/读取/阈值 | `propose → [fill] → approve --yes → compile → verify → watch` |
| **B · 手工 IR** | 任意复杂流程 | `scan → 手写 ir.json → compile --ir → verify → watch` |
| **C · 真实技能** | CSV、自带脚本、L3 动作、bench 用例 | `propose → fill → approve --enable-run-script → compile → verify → measure → bench → watch` |
| **D · op 插件** | 需要自定义确定性计算 | `propose → fill → approve --enable-plugins → compile → verify → bench → watch` |

四条路径都**必须人审**：A/C/D 用 `approve` 显式签发，B 由人直接写 IR。机器草案与 Agent 补丁永远不能直接编译；L2/L3 权限只能在 `approve` 阶段由人显式放通。

### 路径 C：`examples/data-audit/`

含带 BOM 的 CSV、JSON 清单、变更记录、技能自带的 `examples/data-audit/scripts/validate.py`、`examples/data-audit/agent-patch.json` 与 4 个 bench 用例。

```bash
cd scripts
D=../examples/data-audit

python forge.py propose "$D"
python forge.py fill "$D" --patch "$D/agent-patch.json" --by opus
#   打开 build/review-sheet.md 逐条核对，特别是 run_script 那一步
python forge.py approve "$D" --yes --by wwd --enable-run-script   # 唯一可放通 L3 的地方
python forge.py compile "$D"
python forge.py verify "$D" --update-baseline
python forge.py measure "$D" --runs 3            # 自动生成 report.measured.md
python forge.py bench "$D" --runs 3              # 4 个声明式用例 + eval 适配
python forge.py watch "$D"
```

12 个 code 步骤覆盖 13 个内置 op，输出 `STATUS=BLOCK`、`METRIC_NAMES=latency_ms,...`、`VALIDATION_OK=true`。`shell` 刻意不演示：它是 L2 风险，正常流程没有正当用途。

### 路径 D：`examples/plugin-audit/`

声明式插件 `examples/plugin-audit/plugins/risk_score.py` + `examples/plugin-audit/agent-patch.json` + 2 个 bench 用例。

```bash
cd scripts
D=../examples/plugin-audit
python forge.py propose "$D"                 # 自动登记 plugin_ops / plugins_sha256，开关仍为 false
python forge.py fill "$D" --patch "$D/agent-patch.json" --by opus
python forge.py approve "$D" --yes --by wwd --enable-plugins   # 唯一放通口
python forge.py compile "$D"
python forge.py verify "$D" --update-baseline
python forge.py bench "$D" --runs 3
python forge.py watch "$D"                   # 改了 plugins/*.py 立即 DRIFT
```

输出 `STATUS=WARN`、`RISK_SCORE=4`。插件在受限命名空间里运行：`import os` 会被 `ImportError` 拦下，只能 import `json/re/math/...` 白名单模块，且不能 `open()`。

### 路径 A / B

```bash
cd scripts
A=../examples/notes-audit-auto
python forge.py propose "$A" && python forge.py approve "$A" --yes --by wwd
python forge.py compile "$A" && python forge.py verify "$A" --update-baseline
python forge.py verify "$A" --format jsonl && python forge.py watch "$A"

B=../examples/notes-audit
python forge.py compile "$B" --ir "$B/ir.json"
python forge.py verify "$B" --update-baseline && python forge.py watch "$B"
```

A/B 两条路径产出的结构化结果**完全一致**：`STATUS=NEEDS_CLEANUP`、`MD_COUNT=4`、`TODO_HITS=4` 等 7 个键。

## 命令

| 命令 | 作用 | 退出码 |
|---|---|---|
| `scan <dir>` | 静态扫描，写 `build/scan.json` / `scan.md` | 0 |
| `plan <dir>` | 生成空 IR 草案（兼容旧路径） | 0 |
| `propose <dir>` | 自动推断 IR 草案 + 人审清单 + 插件白名单/哈希 | 0 |
| `fill <dir> --patch p.json [--by 来源]` | 合并补丁，仍为草案；非法或提权补丁不写入 | 2 = 校验/策略失败 |
| `approve <dir> [--yes] [--by 姓名] [--enable-shell] [--enable-run-script] [--enable-plugins]` | 人工签发并（仅在此处）放通 L2/L3 | 1 = 未签发，2 = 策略拒绝 |
| `compile <dir> [--ir path] [--force]` | 生成 runner / 精简文档 / 报告 / 锁文件 | 2 = 门禁或护栏失败 |
| `verify <dir> [--update-baseline] [--format jsonl] [--record]` | 跑生成脚本并比对基线 | 1 = 缺基线或回归失败 |
| `watch <dir>` | 检测源文档、产物与 `plugins/` 漂移 | 1 = DRIFT，2 = 未编译 |
| `guard <dir> [--min-runs N]` | 查询流程形状是否稳定 | 1 = NOT_READY |
| `measure <dir> [--runs N] [--record]` | 实测耗时 / 输出字节 / 形状稳定性 | 1 = 有失败运行 |
| `report <dir> [--measurement path]` | 把实测数据合并进不进锁的 `report.measured.md` | 2 = 缺输入 |
| `bench <dir> [--fixtures f.json] [--runs N] [--format jsonl]` | 编译前后离线对照 + eval 适配 | 1 = 用例失败/不稳定 |
| `smoke [root] [--no-zip] [--timeout N]` | 干净目录一键端到端验收 | 1 = 有步骤失败 |
| `rollback <dir> --yes` | 从**原始**备份恢复源 SKILL.md | — |
| `batch <root> [选项]` | 批量扫描技能库（默认只读） | 0 |
| `ci <root> [--strict] [--format jsonl]` | CI 门禁：聚合 watch + verify | 1 = 门禁失败 |
| `doctor [root]` | 上架前自检（含可移植性静态门禁） | 1 = 有问题 |
| `package [root] [--out dir]` | 打包可上传 zip + MANIFEST + UPLOAD | 1 = 回读校验失败 |
| `selftest [root] [-v]` | 内置 unittest 套件（59 条） | 1 = 有用例失败 |

默认 IR：`<dir>/build/ir.json`；默认草案：`<dir>/build/ir.proposed.json`。

## 人审闸门与权限收口（硬规则）

`propose` 生成的 IR 带 `review_status: "PROPOSED"`，每个 step 带 `proposal` 元数据。`IR.validate` **双重拦截**：顶层状态 + 每条残留元数据。`fill` 合并补丁后仍是 `PROPOSED`。唯一解锁方式是 `forge approve <dir> --yes --by <姓名>`。

**L2/L3 只认一个入口**：`approve` 签发时无条件把 `allow_shell` / `allow_run_script` / `allow_plugins` 重置为 `false`，只有三个 `--enable-*` 能放通，并写 `elevated_by` / `elevated_at`；`IR.validate` 要求 `elevated_by == approved_by`。IR 里没有对应特权步骤时，放通请求会被拒绝（避免无谓提权）。

```
fill patch {"allow_run_script": true}  → ERROR 补丁不得自我提权，退出 2
fill patch {"allow_plugins": true}     → ERROR 补丁不得自我提权，退出 2
approve --yes（无 flag，IR 含插件）  → ERROR 需 --enable-plugins，退出 2
approve --yes --enable-plugins        → APPROVED + elevated_by=审阅者
compile                               → OK
```

## IR v1.1：15 个内置 op + 插件 op

| 分组 | op |
|---|---|
| 计数统计 | `count_files` `count_matches` `count_lines` `collect` |
| 读取解析 | `read_json` `read_text` `parse_csv` `json_get` `regex_extract` `pluck` |
| 校验 | `hash_files` `file_exists` |
| 决策 | `decide`（最多 1 个，`emit` 必须为 `status`） |
| 特权（默认关闭） | `shell`（`allow_shell`）、`run_script`（`allow_run_script`）、`plugin_<name>`（`allow_plugins`） |

`run_script` 只允许技能目录内的 `.py`、用当前 Python 解释器执行、`shell=False`、默认 30 秒超时、stdout 截断 64 KiB。**它可执行任意 Python，属 L3 风险**。

`plugin_<name>` 的五道门禁：静态发现（`ast.literal_eval`，从不 import）→ `plugin_ops` 白名单 → `plugins_sha256` 哈希锁定 → 受限命名空间（禁 `os`/`subprocess`/`socket`/`open`）→ `approve --enable-plugins` 人审放通。改动 `plugins/` 任一文件，`watch` 立即 DRIFT。详见 `references/benchmark-and-plugins.md`。

`read_text` / `parse_csv` 用 `utf-8-sig` 解码，兼容 Excel 导出的带 BOM CSV（否则首列名会带 `\ufeff`，`pluck` 静默取不到值）。

编译门禁在生成前校验：溯源行号、op/args 白名单、参数类型与范围、正则合法性、变量声明顺序、保留键冲突、特权开关与签发、插件目录摘要、源摘要、草案状态。完整字段见 `references/ir-schema.md`，空白模板见 `templates/ir.template.json`。

## forge bench：编译前后对照

```bash
cd scripts
python forge.py bench ../examples/data-audit --runs 3
python forge.py bench ../examples/data-audit --format jsonl
```

| 测量 | 不测量 |
|---|---|
| prompt 字符/词/行/字节、运行器确定性、耗时中位数与 P95、声明式用例符合度、工具往返结构值 | 真实模型 token、遵循率、答案质量、成本 |

装了本地 `tiktoken` 才给精确 token 数；否则明确输出 `unavailable`，**不拿字符数冒充 token**。`bench-fixtures.json` 的每个 case 在技能临时副本里覆盖/删除文件后运行 runner，只比对 `expect` 声明的键。

产物：`build/benchmark.json`、`build/benchmark.md`、`build/bench-cases.jsonl`。三者都不进漂移锁，`watch` 仍 CLEAN。`bench-cases.jsonl` 每行带 `prompt_before` / `prompt_after` 全文、场景输入、期望与实际输出、`model_eval: NOT_RUN`，供外部带模型 eval runner 做 A/B。

**小技能可能变长**：`plugin-audit` 实测 378 → 513 字符（+36%）。`bench` 如实报告负值；结晶的主要收益是执行确定性与工具往返，不承诺所有场景都省 prompt。

## NOT_READY 护栏与实测

`compile` 前会检查：最近 5 次运行的输出键集合是否一致、git 近 7 天源文档提交是否超过 4 次。判为 `NOT_READY` 就拒绝编译，`--force` 写 `build/guard-override.json` 留痕；豁免**绑定源 sha256 且 24 小时后自动失效**。新技能无样本时为 `UNSAMPLED`，放行但给出 GUARD 提示。

`measure` 实测墙钟时间、输出字节、形状一致性；`bench` 补上编译前后体积对照与用例符合度。两者**都不测**真实 tokenizer token 与模型步骤遵循率。详见 `references/stability-and-measurement.md` 与 `references/benchmark-and-plugins.md`。

## 批量、CI 与跨平台

```bash
cd scripts
python forge.py batch ~/.workbuddy/skills --out ../reports --name skill-library
python forge.py ci . --strict --format jsonl
python forge.py smoke .          # 完整验收；--no-zip 时用发行清单代替 dist zip
```

- 默认**只读**；`--propose` / `--compile-approved` 才写目标 `build/`，需显式开启。
- `ci` 从不写技能目录、从不自动建基线；缺基线直接判 FAIL。`--strict` 额外拦截「有草案无人审」。
- GitHub Actions 模板是 **3 OS × Python 3.9/3.13** 矩阵并跑 `smoke`；GitLab 是双版本并行；pre-commit 保持轻量只跑 `ci`。
- `doctor` 新增 AST 可移植性门禁：硬编码反斜杠路径、`open/read_text/write_text` 缺 `encoding`、`shell=True`、`subprocess.run` 缺 `timeout`。全项目源码已用 `ast.parse(feature_version=(3, 9))` 校验语法兼容。

## 上架：自检与打包

```bash
cd scripts
python forge.py doctor .        # frontmatter / 版本 / 语法 / 可移植性 / 引用 / 泄漏
python forge.py package .       # dist/skill-forge-1.2.0.zip + MANIFEST.json + UPLOAD.md
python forge.py smoke .         # 用刚生成的 zip 做干净目录验收
```

`doctor` 检查版本一致性、所有 `.py` 语法与 `.json` 合法性、文档内本地引用、发行文件里是否混入本机绝对路径或疑似密钥。`package` 排除 `build/` 与 `reports/`，zip 内带 `MANIFEST.json`（逐文件 sha256），回读做 CRC + 哈希双重校验。`dist/UPLOAD.md` 含可直接粘贴的 description、标签与安装后自检命令。

## 编译产物（都在 `build/`，不覆盖源文件）

| 文件 | 说明 |
|---|---|
| `ir.proposed.json` | 机器草案，`PROPOSED`，不可编译 |
| `review-sheet.md` | 人审清单：来源、置信度、L3 建议、插件声明、未映射行、警告 |
| `agent-patch.applied.json` | `fill` 使用的补丁原文（如有） |
| `ir.json` | `approve --yes` 签发的正式 IR（可能含 `elevated_by`） |
| `scripts/crystallized.py` | 自包含 runner，标准库，可独立分发；插件源码内嵌 |
| `SKILL.md` | 精简版，**始终带可安装 frontmatter**，人审后再替换源文档 |
| `report.md` | 步骤清单、收益估算（非实测）、人审清单；**在漂移锁内** |
| `report.measured.md` | 估算 + 实测合并报告；**不进锁** |
| `benchmark.json` / `benchmark.md` | 编译前后对照；**不进锁** |
| `bench-cases.jsonl` | 外部 eval runner 适配用例；**不进锁** |
| `ir.final.json` | 绑定源 sha256 的最终 IR |
| `crystallize.lock.json` | 源摘要 + 4 个产物哈希 + 签发人 + 插件摘要 |
| `fixtures/baseline.json` | 回归基线（只由 `--update-baseline` 创建） |
| `fixtures/runs.jsonl` | 输出形状采样（只由 `--record` 写入） |
| `measurement.json` / `.md` | `measure` 实测数据 |
| `guard-override.json` | `--force` 豁免留痕（含 `expires_at`，24h TTL） |
| `backup/SKILL.md` | **首次编译前**的原始文档，重复编译不会覆盖 |

## 最小 IR

```json
{
  "version": "1",
  "target": "my-skill",
  "source_sha256": "auto",
  "review_status": "APPROVED",
  "approved_by": "human",
  "allow_shell": false,
  "allow_run_script": false,
  "allow_plugins": false,
  "prohibitions": ["只读，不修改文件"],
  "kept_for_model": ["根据结果撰写结论"],
  "steps": [
    {"id": "s1", "source": "SKILL.md:L12", "owner": "code",
     "op": "count_files", "args": {"glob": "notes/**/*.md"}, "emit": "n"},
    {"id": "s2", "source": "SKILL.md:L15", "owner": "code",
     "op": "decide", "args": {"rules": [{"when": "n > 3", "status": "WARN"}], "else": "OK"},
     "emit": "status"},
    {"id": "s3", "source": "SKILL.md:L18", "owner": "model", "note": "撰写结论"}
  ]
}
```

`target` 用 `ir.slugify_target()` 规范化目录名；目录名含点/空格时会自动转成 `my_skill` 这类合法标识符。

## 与 eval 生态对接

`verify --format jsonl`、`ci --format jsonl` 与 `bench --format jsonl` 每行一个 case，可喂给 Anthropic skill-creator `evals/evals.json`、`skills-evals` 基线快照、阿里 Skill-Up `evals/eval.yaml`。本工具不重复实现 eval runner；`bench-cases.jsonl` 额外提供原文与精简版全文，供带模型 runner 做真实 A/B。

## 目录

```
skill-forge/
├── SKILL.md                  # 可安装技能定义
├── README.md / PRD.md / CHANGELOG.md / LICENSE
├── scripts/                  # CLI、编译器、护栏、基准、插件、批量、CI、探针、smoke、发布
├── references/               # 归属 / IR / Agent / 稳定性 / 输出 / 安全 / CI / 基准与插件
├── templates/                # IR 模板、Agent 补丁模板、CI 三件套
├── evals/                    # 触发与行为评测用例
├── tests/                    # 59 条内置 unittest
├── examples/
│   ├── notes-audit/          # 路径 B：手工 IR
│   ├── notes-audit-auto/     # 路径 A：自动草案
│   ├── data-audit/           # 路径 C：CSV + 自带脚本 + L3 + bench
│   └── plugin-audit/         # 路径 D：声明式 op 插件 + 哈希锁定 + bench
└── dist/                     # package 生成的发行包（不入库）
```

## FAQ

**为什么 propose 不能全自动？** 让模型凭理解改写流程会引入原文没有的要求。IR 强制溯源 + 显式签发，是这个产品的安全边界。

**为什么 `fill` 不能开 `allow_run_script` / `allow_plugins`？** 补丁是机器产物，让它决定权限等于让被审查对象自己签批。L3 只能由人在 `approve` 用显式参数放通，且要留名。

**插件是安全沙箱吗？** 不是。它只是受限命名空间：禁了 `open`/`os`/`subprocess`/`socket`，但 Python 对象内省仍可能逃逸。插件源码必须人工读过再签发，与 `run_script` 同为 L3。

**为什么产物不直接替换 SKILL.md？** 编译是可逆实验。人审清单确认后再替换，`rollback --yes` 一键恢复；重复编译不会覆盖原始备份。

**改了源 SKILL.md 或插件会怎样？** `watch` 返回 DRIFT，`ci` 判 FAIL，`compile` 也会被护栏拒绝。必须重新 propose → 人审 → compile。

**第一次 verify 为什么失败？** 因为没有基线，而工具不会把一次未知好坏的运行当成期望值。先跑 `verify --update-baseline`。

**怎么确认这份压缩包真的能用？** `python scripts/forge.py smoke .`，它会解压 zip 到干净临时目录并跑完 32 步，含三条负例。

## 限制（诚实口径）

- `propose` 是启发式填表助手，不是语义编译器。真实技能库里大量候选是文档示例命令、知识描述或框架特有语法；未映射行与 L3 建议逐条列出，由人决定补写或放弃。
- 低自动映射率是**安全策略的产物，不是 KPI**。新增 op 与插件提供的是「人/Agent 有能力表达」的空间，不是「机器自动放通」的许可。
- `run_script` 能执行技能目录内任意 Python，无法防住脚本自身的联网或写文件。只在脚本可信时开启。
- token 与步骤遵循率的**真实收益**未做带模型基准测试；`report.md` 只给估算，`measure` / `bench` 只测脚本侧与 prompt 体积指标。
- 小技能的精简 SKILL.md 可能比原文更长，`bench` 会如实报告。
- 启发式扫描会误判，必须逐条人审。
- 本地端到端验收只在 Windows / Python 3.13 跑过；Linux/macOS 由 CI 矩阵覆盖，本次未实跑。可移植性静态门禁降低常见故障概率，不等于跨平台已实测通过。
