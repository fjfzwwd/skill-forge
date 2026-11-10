# Owner test：哪一步该交给代码

判定只有一句：**结果是否由输入唯一决定？** 是 → 代码所有；否 → 模型所有。

## 代码所有（可结晶）

| 类别 | 典型信号 | 内置 op |
|---|---|---|
| 计数与统计 | `wc -l`、统计文件数、算通过率、聚合 | `count_files` / `count_matches` / `count_lines` / `collect` |
| 结构化解析 | `jq`、读 JSON/文本/CSV、取字段 | `read_json` / `read_text` / `parse_csv` / `json_get` / `regex_extract` / `pluck` |
| 固定比较 | 阈值判断、semver/哈希/时间戳比较 | `decide` |
| 校验与门禁 | exit code、文件存在性 | `file_exists` + `decide`（IR 无通用 `assert_*`，避免任意表达式） |
| 排序与筛选 | `sort`、按规则过滤、分组 | `collect` + `decide` |
| 固定决策表 | `if X then Y`、`当超过 N 时…` | `decide` |
| 完整性校验 | `sha256sum`、比对快照 | `hash_files` |
| 调用自带脚本 | 固定执行 skill 内的 `.py` | `run_script`（L3，需显式放通与人审，见 `references/agent-authoring.md`） |

## 模型所有（保留在 SKILL.md）

- 意图消歧、判断用户真正要什么
- 写作、总结、命名、语气与取舍
- 评估设计/代码质量、开放式推理
- 需要向人提问（`AskUserQuestion`）的分支
- 解读脚本输出并决定下一步动作

## 静态启发式信号（analyzer 用）

| 信号 | 方向 | 权重 |
|---|---|---|
| fenced ```bash 块 | 强候选 | +3 |
| 行内出现 grep / awk / jq / wc / sort / count | 强候选 | +3 |
| 中文「统计 / 计算 / 校验 / 比较 / 是否存在」 | 候选 | +2 |
| `if … then …` / 决策表 / 「超过 N」 | 候选 | +2 |
| 「总结 / 撰写 / 评估 / 判断 / 权衡 / 命名」 | 保留模型 | −3 |
| `Use when …` 式知识描述 | 跳过 | −4 |
| 含交互提问（`?` / `？`） | 轻微偏模型 | −1 |

得分 ≥ 2 → 建议 code；≤ −2 → 建议 model；中间 → 标 `REVIEW`，交人审。正则会把文档里的**示例命令**也算作候选，所以这只是排序线索，不是事实判定。

## 四条硬规则

1. **溯源**：每个 code 步骤必须带 `source`（原 SKILL.md 行号区间）。无出处的步骤一律拒绝编译。
2. **不发明**：IR 只能重排、下沉原文已存在的动作，不能新增原文没有的要求。
3. **形状未稳不编译**：`forge guard` 依据最近运行的输出键形状与 git 近 7 天 `SKILL.md` 提交数判断；不稳定时返回 `NOT_READY`，`compile` 直接拒绝（`--force` 留痕豁免）。固化一个还在演化的流程比不优化更糟。详见 `references/stability-and-measurement.md`。
4. **推断不等于授权**：`propose` 只是填表助手，无法安全映射的候选必须列入未映射清单；机器草案一律经 `approve --yes` 人工签发后才能编译。
