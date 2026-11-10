---
name: skill-forge
description: >
  把 SKILL.md 里确定性的、每次重复执行的步骤「结晶」成可执行脚本，让大模型只保留真正需要判断的部分。
  很多技能把固定流程写成散文：统计文件数、跑命令、读配置或 CSV、按阈值判断、校验结果，模型每调用一次就重新解释一遍，容易跳步、乱序、输出忽长忽短，还多烧 token 与工具往返。
  本技能提供可审计的编译流水线：静态扫描候选并给原文行号 → 自动推断带置信度与溯源的 IR 草案 → Agent 用补丁补写语义步骤 → 人工签发（未签发草案被门禁拦截；shell、run_script、op 插件只能在签发时用显式参数放通并留名）→ 生成自包含零依赖 Python 运行器与带完整 frontmatter 的精简 SKILL.md → 建回归基线、检测源与插件漂移、按输出形状判断是否 NOT_READY。
  IR v1.1 有 15 个内置 op（计数、匹配、收集、读文本/JSON、解析带 BOM 的 CSV、哈希、文件存在性、正则与列提取、阈值决策，以及需放通的 shell 与 run_script）。v1.2 增加声明式 op 插件：plugins/*.py 静态解析、哈希锁定、受限命名空间（禁 os/subprocess/socket）、L3 人审放通，插件改动即触发 DRIFT。
  还能用 forge bench 做编译前后离线对照：prompt 体积、运行器确定性、耗时、声明式用例符合度，并产出 bench-cases.jsonl 供带模型的 eval runner 做真实 token／遵循率 A/B；**本工具不调用模型，不编造 token 或遵循率数字**。另有实测报告、技能库批量体检、CI 门禁（3 OS × 2 Python）、doctor 自检、package 打包与 smoke 干净目录一键验收。
  全程默认只读、禁网、禁 shell、禁执行技能内脚本与插件；产物写进 build/ 不覆盖源文件，源变更要求重新人审，缺基线不会偷偷创建，强制豁免 24 小时失效。
  适合维护技能库的开发者、压低 Agent 成本或提升流程遵循率的团队、上架前做压缩与回归验证的 Skill 作者。不适合形状未稳、纯知识型或几乎全靠判断的技能。
  触发词：结晶、编译 skill、压缩 skill、把步骤抽成脚本、自动填 IR、IR 草案、Agent 补 IR、run_script、enable-run-script、op 插件、plugin 白名单、插件哈希锁定、enable-plugins、CSV 审计、SKILL.md 太大、token 太高、每次跑结果不一样、步骤遵循率、编译前后对照、bench、eval 适配、NOT_READY、形状稳定性、实测报告、干净目录验收、批量扫描技能库、技能库体检、CI 门禁、漂移检测、回归基线、上架自检、打包技能、smoke、skill forge、crystallize、compile skill、skill compaction、skill audit、doctor、package。
license: MIT
metadata:
  version: 1.2.0
  risk: L1
  requires: python>=3.9
---

# skill-forge · Skill 编译／结晶器

## 何时用

- SKILL.md 里有 bash 块、统计／计数／校验、读配置或 CSV、固定阈值决策表、技能自带脚本或自定义 op。
- 同一个技能跑多次结果不一致，或明显在重复解释同样的步骤。
- 想在上架前压缩体积、建立回归基线、做编译前后对照、自检并打包。
- 想给整个技能库做体检，找出最该硬化的技能，或把漂移检查接进 CI。
- 用户说「把这个 skill 编译一下」「结晶」「压一下 token」「帮我自动填 IR」「加个插件 op」「做编译前后对照」「扫描一下我的技能库」「打包准备上传」「验证这份包能不能跑」。

**不要用于**：还在每天改流程的 skill、纯知识型 skill、以写作／判断为主的 skill。形状未稳时结晶会把错误流程固化；`NOT_READY` 护栏会主动拒绝。

## 工作流（路径 A · 推荐）

对目标 skill 目录 `<dir>` 依次执行（脚本在本技能 `scripts/` 下）：

1. `python scripts/forge.py propose <dir>` — 自动推断 IR 草案 `build/ir.proposed.json` 与人审清单 `build/review-sheet.md`。有 `plugins/` 时同时登记白名单与哈希。
2. 打开清单逐条核对：`source` 行号、op、置信度、推断依据、未映射行、L3 建议、插件声明、警告。**此步不可跳过。**
3. 需要补语义步骤时：`forge fill <dir> --patch my-patch.json --by <来源>`（模板 `templates/agent-patch.json`，完整实例 `examples/data-audit/agent-patch.json`）。补完仍是草案。
4. `forge approve <dir> --yes --by <审阅者>` — 显式签发，产出 `build/ir.json`。**IR 含 `shell` / `run_script` / `plugin_*` 时必须分别加 `--enable-shell` / `--enable-run-script` / `--enable-plugins`**，否则签发被拒；放通会记录 `elevated_by`。
5. `forge compile <dir>` — 先过 NOT_READY 护栏，再生成 `build/scripts/crystallized.py`、`build/SKILL.md`、`build/report.md`、`build/crystallize.lock.json`。
6. `forge verify <dir> --update-baseline`，再 `verify <dir> --format jsonl`；`watch <dir>` 做漂移检查；`measure <dir> --runs 5 --record` 采样形状；`bench <dir>` 做编译前后对照。

路径 B（手工 IR）：`scan` → 手工写 `build/ir.json` → `compile --ir`。M0 旧 IR 缺 `review_status` 时兼容放行并给 WARN。

路径 C（CSV + 自带脚本 + L3）：见 `README.md` 与 `examples/data-audit/`。

路径 D（声明式 op 插件）：见 `examples/plugin-audit/` 与 `references/benchmark-and-plugins.md`。

## 批量、CI、上架

- `forge batch <技能库根目录> --out reports --name skill-library` — 默认只读，输出优先级 JSON + Markdown。
- `forge ci <项目根> --strict --format jsonl` — 漂移 + 回归；`--strict` 额外拦截「有草案无人审」。从不写技能目录、从不自动建基线。
- `forge bench <dir>` — 编译前后离线对照 + `build/bench-cases.jsonl` eval 适配器。**不测真实 token 与遵循率**。
- `forge doctor .` — 上架前自检：frontmatter、版本一致性、Python/JSON 语法、可移植性静态规则、文档引用、绝对路径与密钥泄漏。
- `forge package .` — 生成带逐文件 sha256 清单的 zip、`MANIFEST.json` 与 `UPLOAD.md`，回读校验 CRC。
- `forge smoke .` — **解压发行包到干净临时目录，从零跑 32 步端到端验收（含三条负例）**。判断一份拷贝是否真能独立运行，用这个。
- `forge selftest . -v` — 59 条内置 unittest 回归。
- 模板：`templates/ci/github-actions.yml`（3 OS × 2 Python 矩阵）、`gitlab-ci.yml`、`pre-commit.sh`。

## 硬规则

- 产物在 `build/`，**绝不覆盖源 SKILL.md**。人工确认后才替换；`rollback --yes` 可恢复，且重复编译不会覆盖原始备份。
- 机器草案（`review_status=PROPOSED`）**不可编译**：门禁同时拦截顶层状态与每条 `proposal` 元数据。`fill` 也不绕过。
- 每个 code 步骤必须有 `source`，指向原文确实存在的动作。禁止发明原文没有的要求。
- `approve --yes` 是显式人工签发动作，必须写 `--by` 记录审阅者。
- **L2/L3 权限唯一入口是 `approve`**：签发时无条件重置 `allow_shell` / `allow_run_script` / `allow_plugins` 为 false，只有三个 `--enable-*` 能放通，且要求 `elevated_by == approved_by`。Agent 补丁若试图提权，一律拒绝；IR 里没有对应特权步骤时，放通请求也会被拒绝。
- `run_script` 与 op 插件都是 L3：前者仅技能目录内 `.py`、无 shell、有超时；后者只做静态发现、哈希锁定、受限命名空间（禁 os/subprocess/socket），不保证是安全沙箱，须人工读过源码再签发。
- 源 SKILL.md 或 `plugins/` 任一文件变更后 `watch` 返回 DRIFT，`compile` 也会被护栏拒绝；必须重新 propose → 人审 → compile，或用 `--force` 留痕豁免（绑定源摘要、24 小时自动失效）。
- 缺回归基线时 `verify` 返回 `NO_BASELINE` 并退出 1，**不会**把第一次运行悄悄当成期望值。
- `report.md`（锁内）的收益是估算；实测写进 `report.measured.md`（不进锁）。`bench` 如实报告负值——小技能的精简版可能比原文更长。真实 token 与步骤遵循率仍需外部带模型的 eval runner。
- STATUS=ERROR 时不要猜测，直接把 ERROR 字段报告给用户。

## 归属判定

一句话：**结果是否由输入唯一决定？** 是 → 代码；否 → 模型。详见 `references/owner-test.md`。

## 参考文档

- `references/owner-test.md` — 归属判定与静态信号
- `references/ir-schema.md` — IR 字段、15 个内置 op、插件 op、编译门禁、草案与签发
- `references/agent-authoring.md` — Agent 补丁格式、L3 与插件步骤的正确用法
- `references/benchmark-and-plugins.md` — bench 口径、fixtures、eval 适配、插件五道门禁与受限命名空间
- `references/stability-and-measurement.md` — NOT_READY 护栏、豁免 TTL 与实测口径
- `references/structured-output.md` — 结构化输出约定与收益口径
- `references/safety-policy.md` — 安全边界、人审闸门、权限收口、漂移与回滚
- `references/ci-integration.md` — CI 门禁语义、跨平台矩阵与三种接法

## 示例

- `examples/notes-audit/` — 路径 B：手工 IR 端到端示例。
- `examples/notes-audit-auto/` — 路径 A：自动草案 → 人审签发 → 编译。
- `examples/data-audit/` — 路径 C：CSV（带 BOM）、JSON 清单、变更记录、技能自带脚本、Agent 补丁、L3 放通、实测报告与 4 个 bench 用例。12 个 code 步骤覆盖 13 个内置 op。
- `examples/plugin-audit/` — 路径 D：声明式 op 插件、哈希锁定、`--enable-plugins` 人审放通、插件漂移检测与 2 个 bench 用例。

A/B 两者编译产出的结构化结果**完全一致**：6 个确定性步骤压成 1 次脚本调用，7 个输出键。
