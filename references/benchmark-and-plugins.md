# 基准对照与 op 插件（1.2.0）

## 1. `forge bench`：测什么、不测什么

| 测量 | 口径 |
|---|---|
| Prompt 体积 | 源 `SKILL.md` vs `build/SKILL.md` 的字符、去空白字符、词、行、UTF-8 字节 |
| Token 数 | **仅当本地装了 `tiktoken`** 时用 `cl100k_base` 精确计数；否则输出 `unavailable`，不拿字符数冒充 token |
| 运行器确定性 | 同一输入重复 N 次，`KEY=VALUE` 形状是否完全一致（排除 `ELAPSED_MS`/`STEPS`） |
| 耗时 / 输出量 | 中位数、P95、输出字节中位数，来自运行器自报 `ELAPSED_MS` 与 stdout |
| 声明式用例符合度 | `bench-fixtures.json` 的每个 case 在技能临时副本里覆盖/删除文件，比对 `expect` 中声明的键 |
| 工具往返 | 结构值：IR 中 code 步骤数 → 1 次脚本调用 |

**不测**：真实模型 token 消耗、模型遵循率、答案质量、成本。这些必须有带模型的 eval runner。本工具不编造这些数字。

小技能的精简版可能比原文更长（示例 `plugin-audit`：378 → 513 字符，+36%）。`bench` 如实报告负值；结晶的收益主要在**执行确定性与工具往返**，不是所有场景都省 prompt。

### fixtures 格式

放在 `<skill_dir>/bench-fixtures.json`，或用 `--fixtures <path>` 指定：

```json
{
  "version": "1",
  "cases": [
    {
      "id": "baseline",
      "note": "原始输入",
      "expect": {"STATUS": "BLOCK", "VALIDATION_OK": "true"}
    },
    {
      "id": "few_rows",
      "files": {"data/metrics.csv": "name,value\na,1\n"},
      "remove": ["data/extra.csv"],
      "expect": {"STATUS": "OK"}
    }
  ]
}
```

- `files` / `remove` 的路径都过 `ir.bad_path()`，禁止绝对路径、`..`、URL。
- 每个 case 在**临时副本**里运行，不改动技能目录。
- `expect.STATUS=ERROR` 表示期望运行器按结构化协议报错（同时要求非 0 退出码）。
- 只比对声明的键；`ELAPSED_MS`/`STEPS` 自动排除。

### 输出

| 文件 | 说明 |
|---|---|
| `build/benchmark.json` | 完整结构化结果 |
| `build/benchmark.md` | 可读报告，含口径声明 |
| `build/bench-cases.jsonl` | **外部 eval runner 适配器** |

三者都**不进** `crystallize.lock.json`；`forge watch` 仍为 CLEAN。

`bench-cases.jsonl` 每行含 `prompt_before`（原文全文）、`prompt_after`（精简版全文）、`inputs`、`expected`、`observed`、`diffs`、`model_eval: "NOT_RUN"`。带模型的 runner 可直接对 `prompt_before` / `prompt_after` 做 A/B，并把遵循率/token 结果回写到同一 case_id 上。

```bash
python forge.py bench examples/data-audit --runs 3
python forge.py bench examples/data-audit --format jsonl
```

## 2. op 插件

### 声明

`<skill_dir>/plugins/<file>.py`：

```python
SPEC = {"name": "risk_score", "args": ["weight"], "note": "..."}

def run(ctx, **kwargs):
    import re
    return len(ctx.get("watch_list", [])) * int(kwargs.get("weight", 1))
```

- `SPEC.name`：小写蛇形，决定 op 名 `plugin_risk_score`。
- `SPEC.args`：唯一允许传入的参数白名单。
- `run(ctx, **kwargs)`：第一个参数必须是 `ctx`（前序步骤的所有 emit 值），只读地转换数据。

### 五道门禁

1. `propose` 用 `ast.literal_eval` 读 `SPEC`，**从不 import 插件源码**；登记 `plugin_ops` 白名单 + `plugins_sha256`，`allow_plugins` 保持 `false`。
2. `fill` 可补 `plugin_*` 步骤，但**补丁设 `allow_plugins: true` 一律退出 2**（禁止被审对象自我提权）。
3. IR 校验：插件名必须在 `plugin_ops` 里、`plugins_sha256` 必须是 64 位摘要、`allow_plugins` 必须为 true、必须有 `approved_by` 且 `elevated_by == approved_by`。
4. 唯一放通口：`forge approve <dir> --yes --by <姓名> --enable-plugins`。IR 无插件步骤时该参数会被拒绝（不允许无谓放通）。
5. `compile` 再次核对磁盘上的插件：文件存在、`SPEC.args` 覆盖所有传入参数、目录摘要与 IR 一致。任意一个插件被增/改/删，`forge watch` 报 `DRIFT`。

### 受限命名空间（不是安全沙箱）

生成的 runner 把插件源码内嵌进去，在独立 namespace 里 `exec`：

- 可用内建：计算、容器、异常等白名单；**没有** `open`、`eval`、`compile`、`getattr`、`__import__`。
- 可 import 模块：`json` `re` `math` `csv` `io` `hashlib` `pathlib` `datetime` `collections` `statistics` `itertools` `functools` `string` `decimal` `fractions` `unicodedata` `base64` `binascii` `copy` `operator` `textwrap` `enum` `types` `bisect` `heapq`。
- `os` / `subprocess` / `socket` / `urllib` 等一律 `ImportError`。

设计意图：插件只能处理 runner 已经读进 `ctx` 的数据，不做 IO。但 **Python 对象内省仍可能逃逸**，这不是安全沙箱。插件代码必须当作可信源码人工读过再签发；风险等级 **L3**，与 `run_script` 同级。

### 完整路径

```bash
python forge.py propose examples/plugin-audit
python forge.py fill examples/plugin-audit --patch examples/plugin-audit/agent-patch.json --by <来源>
python forge.py approve examples/plugin-audit --yes --by <姓名> --enable-plugins
python forge.py compile examples/plugin-audit
python forge.py verify examples/plugin-audit --update-baseline
python forge.py bench examples/plugin-audit
python forge.py watch examples/plugin-audit
```

## 3. 可移植性

`forge doctor` 增加 AST 静态门禁：硬编码反斜杠路径、`open()/read_text()/write_text()` 缺 `encoding`、`shell=True`、`subprocess.run` 缺 `timeout`、`os.system` 类调用。

本地端到端只在 Windows / Python 3.13 实测；Linux / macOS 由 CI 矩阵（3 OS × Python 3.9/3.13）覆盖，未在本地实跑。静态检查降低常见跨平台故障概率，但不等于跨平台已实测通过——这点不夸大。
