# 结构化输出约定

结晶的目的不是「把命令搬个家」，而是**把 N 次工具往返 + N 份冗长原始输出，压成一次调用 + 一份紧凑结果**。

## 生成脚本的输出格式

```
=== RESULT ===
STATUS=OK
MD_COUNT=12
TODO_HITS=3
=== ISSUES ===
ISSUE_COUNT=0
=== META ===
ELAPSED_MS=8
STEPS=3
```

规则：

- `=== SECTION ===` 分节，节名固定大写。
- `KEY=VALUE` 一行一个，键为大写下划线，值不含换行；列表用逗号分隔并截断。
- `STATUS` 必填，来自 `decide` op；无 `decide` 时默认 `OK`。
- `ISSUE_COUNT` 必填，便于下游门禁判断。
- 原始命令输出**不进入上下文**；`shell` / `run_script` 的 stdout 会被截断（默认 64 KiB），
  只把结果变量写进 `KEY=VALUE`。
- 失败也输出结构：`STATUS=ERROR`，`ERROR=...`，并补齐 `ISSUE_COUNT=1` 与 `META` 段，
  退出码非 0，不抛 traceback 给模型。

## 收益估算（报告里明确标注为估算）

设原 skill 有 `n` 个可结晶步骤，每次执行：

```
原成本 ≈ SKILL.md_tokens + Σ(cmd_i_tokens + raw_output_i_tokens + reasoning_i_tokens)
新成本 ≈ SKILL.md_compressed_tokens + runner_call_tokens + result_tokens
```

`report.md` 用 12 token/行 做粗估，并显式标注「非实测」。`forge measure` 实测墙钟时间、
输出字节与形状一致性；真实 tokenizer token 与模型步骤遵循率需要外部 eval runner。
**不把估算当实测数据写进上架描述。**

## 精简版 SKILL.md 的形状

```
---
name: <沿用源 name，缺省用 IR.target>
description: <生成的一句话，或 IR.description 覆盖>
license: <沿用源 license，缺省 MIT>
metadata:
  version: <skill-forge 版本>
  generated_by: skill-forge
  risk: L1 | L2 | L3
  requires: python>=3.9
---
# <target>

运行 `python scripts/crystallized.py`，读取 `=== RESULT ===` 段的结构化输出。

按 STATUS 决策：
- OK → <原文的判断性指令>
- NEEDS_CLEANUP → <原文的判断性指令>

模型职责（不要写成脚本、不要重写确定性步骤）...
禁止：...（原文禁止动作原样保留）
```

要点：

- **frontmatter 始终存在且可安装**。源文档没有 frontmatter 时也会生成一份完整的。
- 不照抄源文档冗长的 `description`（它描述的是旧流程，会抵消压缩效果）；
  需要自定义时用 IR 的 `description` 字段覆盖。
- `risk` 由 IR 自动判定：`allow_run_script` → L3，`allow_shell` → L2，否则 L1。
- 判断性指令与禁止动作必须从原文搬运，不得由编译器创作。
