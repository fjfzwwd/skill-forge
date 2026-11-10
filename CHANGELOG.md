# Changelog

## 1.2.0 — 2026-11-10

M3：把「能不能量出收益」和「能不能扩展 op」两件事做成可运行能力，并加上可移植性静态门禁。
不改动 1.1.0 的既有行为；新增能力全部默认关闭或默认只读。

**新增**

- `forge bench` + `scripts/bench.py`：编译前后离线对照。
  - 测 prompt 体积（字符/词/行/UTF-8 字节）、运行器确定性、耗时中位数与 P95、输出字节、声明式用例符合度、工具往返结构值。
  - 仅在本地装了 `tiktoken` 时给精确 token 数；否则输出 `unavailable`，**不用字符数冒充 token**。
  - `bench-fixtures.json` 支持 `files` 覆盖与 `remove` 删除，路径过 `ir.bad_path()`；每个 case 在临时副本运行，不改技能目录。
  - 产出 `build/benchmark.json`、`build/benchmark.md`、`build/bench-cases.jsonl`。三者不进漂移锁。
  - `bench-cases.jsonl` 每行带 `prompt_before` / `prompt_after` 全文、场景输入、期望与实际输出、`model_eval: NOT_RUN`，是接带模型 eval runner 的适配器。
  - **不测**真实模型 token、遵循率、答案质量、成本；不编造数字。小技能精简版可能更长，如实报告负值。
- `scripts/oppack.py` 声明式 op 插件（L3）：
  - `plugins/*.py` 用 `ast.literal_eval` 读 `SPEC`，**编译期从不 import 插件源码**。
  - `propose` 登记 `plugin_ops` 白名单与 `plugins_sha256`，`allow_plugins` 保持 false。
  - 五道门禁：白名单 → 哈希锁定 → 受限命名空间 → `approve --enable-plugins` 人审放通（`elevated_by == approved_by`）→ `compile` 二次核对磁盘。
  - 生成的 runner 内嵌插件源码，在受限 builtins + 白名单 import 下执行；`os`/`subprocess`/`socket`/`open` 被拦。
  - `watch` 覆盖 `plugins/` 漂移；改动任一插件文件即 DRIFT。
  - `fill` 补丁设 `allow_plugins: true` 一律退出 2（禁止自我提权）。
- `examples/plugin-audit/`：路径 D 端到端示例（插件 + Agent 补丁 + 人审放通 + 2 个 bench 用例 + 漂移检测）。
- `examples/data-audit/bench-fixtures.json`：4 个声明式用例（基线 / 少量指标 / 缺列 / 文件缺失按协议报 ERROR）。
- `scripts/portability.py` + `doctor` 可移植性 AST 门禁：硬编码反斜杠路径、IO 调用缺 `encoding`、`shell=True`、`subprocess.run` 缺 `timeout`、`os.system` 类调用。
- `references/benchmark-and-plugins.md`：bench 口径、fixtures 格式、eval 适配、插件五道门禁与受限命名空间说明。

**修复**

- `forge.py` 中 `cmd_bench` 插入时误替换 `cmd_smoke`，导致 CLI 启动 `NameError`（已恢复并加进测试）。
- `oppack.check` 初版要求 `plugin_ops` 与实际使用的插件集合完全相等，会误拒「声明多个可用插件、只用其中一个」的技能；改为子集校验。
- `bench` 首版对 `expect.STATUS=ERROR` 的场景按普通失败处理；现按退出码 + STATUS 双判定期望错误。

**验收**

- `doctor` 64/64 PASS，`selftest` 59/59 OK，`ci --strict` 4 skill / 8 检查 / 0 失败。
- `smoke` 用最终发行包解压后跑 32 步全通过（含三条负例）。
- 独立解压复验：CRC + 逐文件 sha256 全一致，包内无 `build/` / `reports/` / `dist/` / `.pyc`。
- 跨平台：本地仅 Windows / Python 3.13 实跑；Linux/macOS 由 CI 矩阵覆盖，未实跑。已加静态门禁降低常见故障概率。

## 1.1.0 — 2026-11-10

用第三个真实感示例做端到端压测，暴露并修掉 9 个会影响「装上去就能用」的缺陷；
同时把实测数据、一键验收与跨平台验证补进产品。

**新增**

- `examples/data-audit/`：含 CSV（特意带 UTF-8 BOM）、JSON 清单、变更记录、
  技能自带的 `examples/data-audit/scripts/validate.py` 与 `examples/data-audit/agent-patch.json` 完整示例。演示
  `propose → fill → approve --enable-run-script → compile → verify → measure → report → watch`。
  12 个 code 步骤覆盖 13 个内置 op；`shell` 刻意不演示（L2 风险，无正当用途）。
- `forge report`：把 `measure` 的实测数据合并成 `build/report.measured.md`。
  该文件**不进漂移锁**，因此 `report.md`（锁内）保持确定性，`watch` 仍为 CLEAN。
  `measure` 会自动调用一次。
- `forge smoke`：干净目录一键验收。优先解压 `dist/` 里的最新发行包（校验 CRC），
  没有包时复制发行清单；随后从零跑 23 步，含两条负例（未放通 L3、直接编译草案）。
  这是「完整可运行」的可重复证明，不依赖工作区或预建产物。
- `approve --enable-shell` / `--enable-run-script`：**L2/L3 权限唯一入口**。
  签发时无条件把两个开关重置为 false，只有显式参数才放通，并写
  `elevated_by` / `elevated_at`；IR 里没有对应特权步骤时拒绝无谓放通。
- `--force` 豁免加 24 小时 TTL、绑定源 sha256、写 `expires_at`；过期或源变更即失效，
  `watch` 会提示当前豁免状态。
- `templates/ci/github-actions.yml` 改为 3 OS × 2 Python 版本矩阵，并跑 `smoke`；
  GitLab 模板改为 3.9 / 3.13 双版本并行。
- 测试 33 → 45 条；新增 12 条针对本轮修复的回归用例。

**修复（均可复现）**

- **生成的运行器用 `json.dumps` 拼 Python 字面量**：`json_get` 的 `default` 只要不是
  字符串就会生成 `false` / `null` 并抛 `NameError`；省略 `default` 必崩。改用 `repr()`。
- **UTF-8 BOM**：运行器用 `utf-8` 解码 CSV，首列名变成 `\ufeffname`，导致
  `parse_csv → pluck` 与表头校验静默失效。改用 `utf-8-sig`。
- **`fill` 完全无法补写 `run_script`**：结构探针缺 `approved_by` / `elevated_by`，
  且草案开关为 false，任何含 L3 步骤的补丁都被误拒。M2 提高覆盖率的关键路径是断的。
  现探针只验结构（临时镜像开关 + 一次性签名），权限由 `approve` 决定。
- **Agent 补丁可自我提权**：补丁能设 `allow_shell` / `allow_run_script: true`，
  只给 WARN；附带的 `templates/agent-patch.json` 就带着这个字段，按其 `_hint` 使用
  会失败。现提权补丁一律拒绝，模板已改。
- **补丁注释键被当未知键**：`_hint` 遭拒；现忽略所有 `_` 前缀键。
- **`proposal.suggestions.patch_hint` 自带提权开关**，与新策略冲突；现改为
  `approve_hint` 指向显式人审命令。
- **禁止动作误收 Markdown 标题与模型职责描述**：`## 禁止`、`这部分需要判断…`
  被写进 `prohibitions`，污染精简版 SKILL.md。
- **目录名含点/空格时全链路失败**：`target` 直接用目录名，`IR.validate` 判非法标识符。
  现统一 `ir.slugify_target()`，`expect_target` 也按同规则比对。
- **`doctor` 引用检查把示例内的相对路径按仓库根解析**，误报缺失；现同时尝试
  相对该文档所在目录。
- **发行清单漏掉新示例的 `data/`、`scripts/`、补丁文件**，打包会缺文件；已补齐。

## 1.0.0 — 2026-11-10

第一个可上架的正式版。目标是「装上去就能用、上传前能自检、出错能拦住」。

**新增**

- IR v1.1：15 个内置 op。新增 `run_script`、`read_text`、`parse_csv`、`count_lines`、
  `file_exists`、`regex_extract`、`pluck`，覆盖真实技能里最常见的「调用自带脚本 /
  解析外部格式」动作。
- `run_script` 是显式 L3 逃生口：只允许技能目录内的 `.py`、无 shell、有超时、
  需要 `allow_run_script=true` 且 IR 必须有 `approved_by` 签发人。propose 不会自动放通，
  只把候选写入 `proposal.suggestions`，由人用 `forge fill` 显式补。
- `forge fill`：Agent 或人提供 JSON 补丁，合并进草案并做完整 IR 校验；结果仍是
  `PROPOSED`，不绕过人审闸门。补丁原文存 `build/agent-patch.applied.json`。
- `forge guard` / compile 内置 NOT_READY 护栏：依据最近 N 次运行的输出键形状与
  git 近 7 天源文档改动次数判断流程是否稳定；不稳定直接拒绝编译，`--force` 留痕豁免。
- `forge measure`：实测墙钟时间、输出字节、输出形状一致性，报告明确标注不含真实
  tokenizer token 与模型遵循率。
- `forge doctor`：上架前自检（frontmatter / 版本一致 / .py 语法 / .json 合法 /
  文档引用 / 绝对路径与密钥泄漏 / __pycache__）。
- `forge package`：产出带 `MANIFEST.json`（逐文件 sha256）的 zip、回读 CRC 校验、
  `UPLOAD.md` 上架说明；包内不含 `build/` 与 `reports/`。
- `forge selftest`：内置 unittest 套件，覆盖 IR 门禁、路径穿越、新 op、回归、漂移、
  护栏、批量、CI、打包与 CLI。
- 生成的精简版 `SKILL.md` 始终带完整可安装 frontmatter（name / description /
  license / metadata.version / risk），不再照抄冗长的旧 description。
- `LICENSE`、`references/agent-authoring.md`、`references/stability-and-measurement.md`、
  `templates/agent-patch.json`、`tests/`。

**修复（均为会影响正确性的真 BUG）**

- `verify` 在缺基线时曾静默创建基线并返回 PASS；现在只有显式 `--update-baseline`
  才写基线，否则返回 `NO_BASELINE` + 退出 1。CI 与 `batch --compile-approved` 已适配。
- 编译期路径校验漏掉 Windows 盘符与 UNC 绝对路径；现统一由 `ir.bad_path` 拦截，
  运行时仍有 `relative_to(ROOT)` 二次防护。
- 重复编译曾覆盖 `build/backup/SKILL.md`，会毁掉唯一的原始文档副本；现只在首次创建。
- 源文档变更后（IR 写 `source_sha256: auto`）可直接重新编译，绕过漂移语义；
  现由 `_drift_guard` 拦截，`--force` 才允许并写 `override-audit.json`。
- 无 frontmatter 的 SKILL.md 被正文中的 `---` 水平线误判为 frontmatter，导致后续
  内容整段被吞；analyzer 与 proposer 统一改为「只有首行 `---` 才开启」。
- 子进程未指定编码，Windows 非 UTF-8 代码页下中文输出会崩；现全部强制 UTF-8 +
  `errors=replace`，runner 也 `reconfigure` stdout/stderr。
- 子进程超时未捕获会让 `verify` 抛栈；现返回 FAIL。
- 生成 runner 的 `--root` 缺参数会 IndexError；现返回结构化 `STATUS=ERROR`。
- 非法正则 / 错误参数类型 / 未定义变量 / 与协议保留键冲突的 emit 过去要到运行期才炸，
  现全部在编译门禁拦截。
- `decide.when` 的右值、状态标识符合法性在编译期校验。
- 版本号散落在 5 处字符串（0.1.0 / 0.2.0 混用）；现统一 `scripts/version.py`，
  `doctor` 会检查代码与文档一致。

**口径**

- 覆盖率探针与批量报告都是启发式统计，不是实测收益；报告内显式标注。
- `run_script` 可执行技能目录内任意 Python，风险等级 L3，默认关闭。

## 0.2.0 — 2026-11-10

M1：`propose` / `approve` 人审闸门、批量扫描 `batch`、CI 门禁 `ci`、
`core.py` 复用层、真实技能库只读报告。

## 0.1.0 — 2026-11-09

M0：`scan` / `plan` / `compile` / `verify` / `watch` / `rollback`、
Crystallize IR v1（8 个 op）、自包含 runner、精简 SKILL.md、报告、锁文件、回归基线。
