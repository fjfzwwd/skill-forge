# Crystallize IR v1.1

编译器的唯一中间表示。人审的对象是 IR，不是生成的代码。IR 是可读 JSON，可进 Git、可 diff、可签名。

## 顶层结构

```json
{
  "version": "1",
  "target": "notes-audit",
  "source": "SKILL.md",
  "source_sha256": "<64 hex>",
  "review_status": "APPROVED",
  "approved_by": "human",
  "approved_at": "2026-11-10T06:40:00",
  "allow_shell": false,
  "allow_run_script": false,
  "allow_plugins": false,
  "plugin_ops": [],
  "plugins_sha256": "",
  "elevated_by": "human",
  "elevated_at": "2026-11-10T06:40:00",
  "steps": [],
  "gates": [],
  "prohibitions": [],
  "kept_for_model": []
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `version` | 是 | 固定 `"1"`（op 集合为 v1.1，IR 结构版本仍为 1） |
| `target` | 是 | 由 `ir.slugify_target(目录名)` 规范化出的合法标识符（`^[A-Za-z_][A-Za-z0-9_-]*$`）；目录名含点/空格也能编译。与规范化后的目录名不一致给 WARN |
| `source_sha256` | 是 | `auto` = 编译绑定当前源（开发用）；生产写死 64 位摘要，编译期校验 |
| `review_status` | 建议 | `APPROVED` / `PROPOSED` / 缺省。`PROPOSED` **一律拒绝编译**；缺省按旧 IR 兼容放行并给 WARN |
| `approved_by` / `approved_at` | 特权 op 必填 | `forge approve --yes` 写入的签发记录；`shell` / `run_script` 要求非空 `approved_by` |
| `allow_shell` | 否（默认 false） | 为 `true` 才允许 `shell` op（L2）。**只能由 `approve --enable-shell` 写入** |
| `allow_run_script` | 否（默认 false） | 为 `true` 才允许 `run_script` op（L3）。**只能由 `approve --enable-run-script` 写入** |
| `allow_plugins` | 否（默认 false） | 为 `true` 才允许 `plugin_<name>` op（L3）。**只能由 `approve --enable-plugins` 写入** |
| `plugin_ops` | 插件 op 必填 | 小写蛇形插件名白名单；`propose` 自动登记，签名后不得增删 |
| `plugins_sha256` | 插件 op 必填 | `plugins/` 全目录哈希（路径 + 声明名 + 文件字节）；任一插件增/改/删都会与 `watch` 冲突 |
| `elevated_by` / `elevated_at` | 特权 op 必填 | 谁放通了 L2/L3；`IR.validate` 要求 `elevated_by == approved_by`。Agent 补丁设不了这个字段 |
| `proposal` | 仅草案 | 机器草案元数据（生成器、候选统计、未映射行、L3 建议、警告）。`approve` 时整体剥离 |
| `patched_by` / `patched_at` | 否 | `forge fill` 写入的补丁审计信息 |
| `description` | 否 | 覆盖生成的精简版 SKILL.md description |
| `steps` | 是 | 有序步骤列表 |
| `gates` | 否 | 全局门禁标签；出现 `allow_network` 直接拒绝 |
| `prohibitions` | 建议 | 原文中的禁止动作，原样带到精简版 SKILL.md |
| `kept_for_model` | 是 | 明确保留给模型的判断，编译后逐条核对未被误删 |

## Step

```json
{
  "id": "s2",
  "source": "SKILL.md:L16-L18",
  "owner": "code",
  "op": "count_matches",
  "args": { "glob": "**/*.md", "pattern": "TODO" },
  "emit": "todo_hits",
  "note": "统计含 TODO 的笔记数"
}
```

| 字段 | 说明 |
|---|---|
| `id` | 稳定标识，步骤顺序变化时尽量不复用 |
| `source` | **必填**，原 SKILL.md 行号区间，门禁校验行号存在且不越界 |
| `owner` | `code` 或 `model`；`model` 步骤不生成代码，只记录 |
| `op` | 见下表，必须在内置 op 白名单内，或为 `plugin_<name>` 且 name 在 `plugin_ops` 里 |
| `args` | 内置 op 只允许该 op 声明的键；插件 op 只允许该插件 `SPEC.args` 声明的键，且值必须是 JSON 标量/数组/对象 |
| `emit` | 输出变量名，合法标识符、全局唯一、不得与协议保留键（`status`/`issue_count`/`steps`/`error`/`elapsed_ms`/`result`/`issues`/`meta`）冲突 |
| `note` | 可选，人读 |
| `proposal` | **仅机器草案**：`confidence` / `reason` / `matched_text`。正式 IR 中残留即门禁失败 |

## 机器草案、Agent 补丁与人工签发

`forge propose` 从原文行启发式推断 op/args/source，同时提取 `kept_for_model` 与 `prohibitions`，产出 `build/ir.proposed.json` 与 `build/review-sheet.md`。三条保守原则：

1. **宁缺毋滥**：无法安全映射到内置 op 的候选列入 `proposal.unmapped`，不猜测。
2. **可解释**：每个 step 带置信度、推断依据与命中原文；独立 bash 块推断一律 `low`。
3. **不可执行**：顶层 `review_status=PROPOSED` + 每条 `proposal` 元数据构成双重门禁。

遇到 `python scripts/xxx.py` 这类调用时，propose **不生成** `run_script` step，而是写入 `proposal.suggestions`（含可直接使用的 `patch_hint` 与 `approve_hint`）。语义补全走 `forge fill`，补丁合并后仍为 `PROPOSED`，且立刻跑完整 IR 校验；不合法则不写入。`fill` 的校验探针带一次性签发人并临时镜像特权开关，只验结构、不授予权限；探针内容绝不写进草案。补丁若把三个 `allow_*` 中任一个设为 `true` 会直接退出 2。详见 `references/agent-authoring.md`。

有 `plugins/` 目录时，`propose` 会用 `ast.literal_eval` 静态读出每个插件的 `SPEC`，把 `plugin_ops` 与 `plugins_sha256` 写进草案，**但 `allow_plugins` 保持 false**；人审清单会列出插件声明。插件源码在编译期从不被 import。

`forge approve <dir> --yes --by <审阅者>` 是**唯一解锁动作，也是 L2/L3 唯一放通入口**：剥离所有 `proposal` 元数据、写 `APPROVED` 与签发记录、**无条件把三个特权开关重置为 `false`**，只有 `--enable-shell` / `--enable-run-script` / `--enable-plugins` 能重新放通并写 `elevated_by` / `elevated_at`。IR 含特权步骤却未放通、或没有特权步骤却要求放通，都返回 2。不加 `--yes` 只打印人审清单并返回 1。

## 内置 op（15 个，零依赖、跨平台）

| op | args | 输出 | 风险 |
|---|---|---|---|
| `count_files` | `glob`, `root?` | int | L1 |
| `count_matches` | `glob`, `pattern`, `root?`, `mode?`(`files`/`lines`) | int | L1 |
| `count_lines` | `glob`, `root?` | int（总行数） | L1 |
| `collect` | `glob`, `root?`, `limit?`(1..10000) | list[str] | L1 |
| `read_json` | `path` | dict/list | L1 |
| `read_text` | `path`, `max_bytes?` | str | L1 |
| `parse_csv` | `path`, `limit?` | list[dict] | L1 |
| `json_get` | `from`, `key`, `default?` | any（支持 `a.b` 路径） | L1 |
| `regex_extract` | `from`, `pattern`, `group?`, `limit?` | list[str] | L1 |
| `pluck` | `from`, `key`, `limit?` | list（从 list[dict] 取列） | L1 |
| `hash_files` | `glob`, `root?` | str (sha256) | L1 |
| `file_exists` | `path` | bool | L1 |
| `decide` | `rules` `[{"when":"todo_hits > 5","status":"NEEDS_CLEANUP"}]`, `else?` | str (status) | L1 |
| `shell` | `cmd`, `timeout?`(1..300) | str，**仅 `allow_shell=true`** | L2 |
| `run_script` | `path`, `argv?`, `timeout?`, `max_bytes?` | str 或 JSON，**仅 `allow_run_script=true`** | L3 |

### 插件 op（1.2.0）

| op | args | 输出 | 风险 |
|---|---|---|---|
| `plugin_<name>` | 由该插件 `SPEC.args` 声明 | 插件 `run(ctx, **kwargs)` 的返回值 | L3 |

`ctx` 是前序步骤所有 `emit` 的字典，插件只读地转换数据。生成 runner 时插件源码内嵌，在受限 builtins + 白名单 import 下执行：`os` / `subprocess` / `socket` / `open` 均不可用。这是**受限命名空间，不是安全沙箱**（对象内省仍可能逃逸），必须人工读过源码再签发。

`plugin_ops` 与 `plugins_sha256` 由 `propose` 登记；`oppack.check` 在编译前二次核对：插件文件存在、`SPEC.args` 覆盖所有传入参数、目录摘要与 IR 一致。`watch` 也比对 `plugins/` 摘要，任一文件变化即 DRIFT。完整规则见 `references/benchmark-and-plugins.md`。

`from` 引用的变量必须在更靠前的步骤里 `emit` 过；`decide.when` 的左值同理。`decide` 最多 1 个且 `emit` 必须为 `status`；无 `decide` 时 `STATUS` 恒为 `OK`。`status` / `else` 的值必须是合法标识符。

`root` 与所有 `path` 一律相对 skill 目录，禁止绝对路径（含 Windows 盘符与 UNC）、`..`、URL 与控制字符。`decide.when` 只支持比较表达式 `> < >= <= == !=`，右值只允许数字/布尔/带引号字符串，**不使用 `eval`**。

`read_text` / `parse_csv` 用 `utf-8-sig` 解码，兼容 Excel 导出的带 BOM CSV；否则首列名会带 `\ufeff`，`pluck` 会静默取不到值。

生成运行器时，参数值用 `repr()` 渲染为 **Python 字面量**（不是 JSON）：`default: false` / `default` 省略分别生成 `False` / `None`，避免生成 `false` / `null` 导致 `NameError`。

`run_script` 的额外边界：仅技能目录内 `.py`、用当前 Python 解释器直接执行、`shell=False`、默认 30 秒超时、stdout 截断 64 KiB、非 0 退出码报错、运行时二次校验路径未越出技能目录。它需要 `allow_run_script=true`、非空 `approved_by` 与 `elevated_by == approved_by`。**它可执行任意 Python，无法防住脚本自身联网或写文件**，只在脚本可信时开启。

## 编译门禁（任一失败即中止，退出码 2）

1. 每个 `code` step 有合法 `source`，行号在源文件范围内。
2. `op` 在白名单内；`args` 键不超出声明；参数类型与范围合法（正则可编译、limit/timeout/max_bytes/group 为整数且在范围内、mode 枚举合法、argv 为标量数组）。
3. `emit` 唯一、合法标识符、不与协议保留键冲突。
4. `json_get` / `regex_extract` / `pluck` 的 `from` 与 `decide.when` 左值引用的变量均已在前序 step 声明。
5. `shell` / `run_script` / `plugin_<name>` 需对应顶层开关，且 IR 有非空 `approved_by`；`APPROVED` 的 IR 还要求 `elevated_by == approved_by`（缺省按旧 IR 给 WARN 放行）；`run_script.path` 必须是目录内 `.py`。
6. `allow_shell=false` 时不存在 `shell` step；插件名必须在 `plugin_ops` 里，`plugins_sha256` 必须是 64 位摘要；`gates` 中不得出现 `allow_network`。
7. `source_sha256` 与当前文件一致；源文档与上次编译锁文件不一致时由漂移护栏另行拦截；`plugins/` 摘要由 `oppack.check` 与 `watch` 单独校验。
8. `review_status` 不是 `PROPOSED`，所有 step 都不带 `proposal` 元数据；未知状态值同样拒绝。
9. `prohibitions` / `kept_for_model` 为空时给 WARN（不阻断），提示该 skill 可能应整体改写成脚本或漏了禁止动作。
10. 编译前跑 NOT_READY 护栏；形状不稳定时拒绝，除非 `--force` 留痕。

## 与现有 eval 生态对接

`verify --format jsonl`、`ci --format jsonl` 与 `bench --format jsonl` 每行一个 case，可喂给 Anthropic skill-creator `evals/evals.json`、`skills-evals` 基线快照、阿里 Skill-Up 的 eval.yaml。本工具不重复实现带模型的 eval runner；`measure` 只测脚本侧的耗时、字节与形状一致性，`bench` 只测 prompt 体积与声明式用例符合度，并把原文/精简版全文放进 `bench-cases.jsonl` 供外部 runner 做真实 A/B。
