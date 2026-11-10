# Agent 协助填写 IR（语义提案）

`propose` 是**启发式填表助手**，只认固定句式；它不懂语义，也不该猜。真实技能里有大量
「调用 skill 自带的脚本」「解析某个外部格式」的动作，正则无法安全映射。这部分交给
Agent（或人）通过 `forge fill` 显式补写，是本产品把「语义能力」接进来的唯一入口。

## 为什么不让模型直接生成 IR 再编译

模型凭理解改写流程会引入原文没有的要求，而结晶会把这个错误固化成代码、锁进基线、
接进 CI。所以本工具把语义补全放在**草案阶段**，并且补完之后仍然需要人审签发：

```
propose  →  fill（Agent 补语义步骤）  →  approve --yes --by <姓名> [--enable-*]  →  compile
  机器草案       仍是 PROPOSED              唯一解锁 + 唯一放通入口
```

`fill` 会立刻对合并后的 steps 跑一遍完整 IR 校验；不合法就不写入，草案保持原样。
校验用的「签发人」和「特权开关」是**一次性探针**，只用于检查结构，绝不写进草案。

## 补丁格式

见 `templates/agent-patch.json` 与完整实例 `examples/data-audit/agent-patch.json`。
顶层只接受这些键，多一个就拒绝（`_` 前缀的键是注释，会被忽略）：

| 键 | 类型 | 作用 |
|---|---|---|
| `by` | str | 补丁来源标识，写进 `patched_by` |
| `steps_add` | array | 追加步骤 |
| `steps_replace` | object | `step.id` → 新步骤对象；不存在的 id 会给 WARN 并忽略 |
| `steps_remove` | array | 要删除的 `step.id` |
| `prohibitions_add` / `kept_for_model_add` | array | 追加禁止动作 / 模型职责 |
| `allow_shell` / `allow_run_script` / `allow_plugins` | bool | **只能设为 `false`（收紧）**；设为 `true` 一律拒绝 |
| `plugin_ops` / `plugins_sha256` | array / str | 插件白名单与目录摘要；通常由 `propose` 自动登记 |
| `description` | str | 覆盖生成的精简版 description |

**补丁不得自我提权。** 让机器产物决定权限，等于让被审查对象自己签批。L2/L3 只能在
`forge approve` 由人用 `--enable-shell` / `--enable-run-script` / `--enable-plugins` 放通，
并写 `elevated_by` 留名；IR 里没有对应特权步骤时，放通请求也会被拒绝。

补丁原文会存到 `build/agent-patch.applied.json`，人审清单 `review-sheet.md` 会重新生成，
每条补丁步骤标 `confidence=agent`，一眼可辨。补丁含 `run_script` / `shell` / `plugin_*` 时，
`fill` 会明确提示签发阶段需要哪个 flag。

## 每个 step 必须满足

- `source` 指回原 SKILL.md 中真实存在的行（格式 `SKILL.md:L42` 或 `SKILL.md:L42-L45`）。
  **不能发明原文没有的动作**——这是硬性约束，填充时若原文只是示例命令，应放弃而不是补进去。
- `owner` 为 `code` 或 `model`。判断标准见 `references/owner-test.md`：
  结果是否由输入唯一决定？是 → code，否 → model。
- `op` 必须在 `references/ir-schema.md` 的 15 个内置 op 白名单内，或为 `plugin_<name>` 且 name 已在 `plugin_ops` 里；`args` 键不能超出内置 op 或插件 `SPEC.args` 的声明。
- `emit` 唯一、合法标识符、不与协议保留键（`status` / `issue_count` / `steps` / `error` …）冲突。
- 引用前序变量时（`json_get` / `regex_extract` / `pluck` 的 `from`，`decide.when` 的左值），
  该变量必须在更靠前的步骤里 `emit` 过。
- `target` 由 `propose` 用 `ir.slugify_target()` 规范化，目录名含点/空格也会得到合法标识符。

## L3：`run_script` 的正确用法

`propose` 遇到 `python scripts/xxx.py` 时**不会**自动映射，而是写进
`proposal.suggestions`，附 `patch_hint`（可直接作为补丁使用）与 `approve_hint`
（人审放通命令）。原因：这条命令可能是文档示例，也可能是固定流程；自动放通等于让工具
执行任意 Python。

要启用，必须同时满足：

1. `forge fill` 补丁里补一条 `run_script` step，`args.path` 是技能目录内的 `.py`；
   **补丁里不要写 `allow_run_script`**；
2. 人审执行 `forge approve <dir> --yes --by <姓名> --enable-run-script`，
   签发时写 `allow_run_script=true` + `approved_by` + `elevated_by`，且两者必须一致；
3. 生成的 runner 运行时再次校验路径未越出技能目录，使用 `sys.executable` 直接执行、
   `shell=False`、`timeout` 默认 30 秒、stdout 截断 64 KiB、非 0 退出码直接报错。

`run_script` 能执行技能目录内任意 Python 代码，**不能防住脚本自身的网络访问或写文件**。
把它当 L3 风险看待：只在脚本来源可信、且你已读过脚本内容时开启。默认关闭。

## L3：`plugin_<name>` 的正确用法

技能目录有 `plugins/` 时，`propose` 会静态读出每个插件的 `SPEC`，把
`plugin_ops` 与 `plugins_sha256` 写进草案（`allow_plugins` 仍为 `false`），并在
`review-sheet.md` 的「插件声明」段列出白名单与摘要。编译期从不 import 插件源码。

1. 人先逐个打开 `plugins/*.py`，确认它只做确定性数据转换、没有 IO 意图。
2. `forge fill` 补丁里补 `plugin_<name>` 步骤，`args` 只用该插件 `SPEC.args` 声明的键；
   **补丁里不要写 `allow_plugins`**。
3. 人审执行 `forge approve <dir> --yes --by <姓名> --enable-plugins`。
4. `compile` 再次核对插件目录摘要、参数白名单与文件存在性；之后任何插件文件变化都会让 `watch` 报 DRIFT。

插件在受限命名空间运行，禁 `open` / `os` / `subprocess` / `socket`，但**不是安全沙箱**。
完整规则见 `references/benchmark-and-plugins.md`，可运行实例见 `examples/plugin-audit/agent-patch.json`。

## 一个完整例子

`examples/data-audit/` 是可直接运行的完整实例（CSV + 自带脚本 + 补丁 + L3 放通 + 实测）：

```bash
cd scripts
D=../examples/data-audit

python forge.py propose "$D"
# 打开 build/review-sheet.md 的「L3 建议」段，确认脚本调用确属固定流程
python forge.py fill "$D" --patch "$D/agent-patch.json" --by opus
python forge.py approve "$D" --yes --by wwd --enable-run-script
python forge.py compile "$D"
python forge.py verify "$D" --update-baseline
python forge.py measure "$D" --runs 3
python forge.py bench "$D" --runs 3
python forge.py watch "$D"
```

若从空白模板开始：

```bash
cp ../templates/agent-patch.json "$D/my-patch.json"
# 编辑 source / args.path / steps_add，不要加 allow_* = true
python forge.py fill "$D" --patch "$D/my-patch.json" --by opus
```

## 反例（不要这么做）

- 不要在补丁里写 `allow_run_script: true` / `allow_shell: true` / `allow_plugins: true`：`fill` 会退出 2 并拒绝写入。
- 不要用插件绕过 `run_script` 的审查：插件同样要人读源码、同样要 `--enable-plugins`、同样受哈希锁定。
- 不要为「让覆盖率好看」把 `proposal.unmapped` 里的每一行都补成 `run_script`。
  未映射往往正说明它是知识描述或一次性示例。
- 不要用 `steps_replace` 悄悄把一个只读步骤换成 `shell`，指望人审看不出来：
  清单会标 `confidence=agent`，且 `approve` 会要求对应 `--enable-*`。
- 不要在 `source` 里写一个不存在的行号来「凑格式」：门禁会校验范围，但更重要的是
  溯源会失真，之后没人能判断这段代码到底来自哪句要求。
