# skill-forge PRD · Skill 编译／结晶器

版本 1.2.0 · 2026-11-10 · 状态：**可上架正式版**，代码、59 条测试、四个端到端示例、编译前后基准、声明式 op 插件、干净目录验收与发行包均已交付。

## 1. 一句话定位

给 Skill 开发者用的编译器：把 SKILL.md 里确定性的重复步骤抽成自包含脚本，让大模型只保留真正需要判断的部分。**卖铲子，不淘金**——不做第 18000 个业务 Skill，做那 18000 个 Skill 都要用的工具。

## 2. 问题与证据

Skill 是 Markdown 里的自然语言流程。模型每次调用都要重新解释步骤、决定下一条命令、读完冗长原始输出再推理。后果：跳步、乱序、结果波动、token 与工具往返浪费。

| 证据 | 数据 | 来源 |
|---|---|---|
| 遵循率 | 直接执行 SKILL.md 时 26.2–54.0%；编译成 harness 后最高 100% | SIGIL, arXiv 2607.27309 |
| token | 四个运行时模型中三个降低 21–45% | 同上 |
| 任务表现 | SkillsBench 绝对提升 3.5–35.7 pp | 同上 |
| 手工案例 | 把稳定流程改成 Python 后 token −94%、延迟 −87% | Vivek Haldar 案例（agentrex 报道） |
| 存量结构 | 某 308 个 SKILL.md 的仓库中仅 17 个带脚本 | ADR-0016, laurigates/claude-plugins |
| 生态规模 | ClawHub 约 18,142 技能、日增约 1000；开发工具 35% + 办公效率 25% | 清华清新研究 2026 / ClawHub 分类数据 |
| 供给空白 | 上述能力目前只有论文原型 + 一个社区 `crystallize` 脚本，无可安装产品、无中文生态 | 本次市调 |

数字来自不同时点与二手报告，趋势一致但绝对值不可混用；**均为外部数据，不是本产品实测**。

供给侧已挤满的方向：评测框架（6+）、静态扫描、词法冲突 lint、token 归因（Claude Code 原生 OTel）、lockfile 漂移。真空在「给 Skill 开发者用的、可审计的编译工具链」。

变现现实：某实测 1,352 次下载只装了 10 个、收入 $0；安全审查还要求开源源码，用户看完直接复制。因此壁垒必须是**脚本 + 门禁 + 持续数据**，不是单个 Markdown 文件。

## 3. 用户与场景

| 用户 | 场景 | 成功标准 |
|---|---|---|
| 维护技能库的开发者 | 找出最该硬化的技能、批量编译、接 CI | 一次扫描出优先级；`ci --strict` 能挡住漂移 |
| 准备上架的 Skill 作者 | 压缩体积、建回归基线、自检、打包 | `doctor` PASS、`package` 出可上传 zip |
| 压 Agent 成本的团队 | 减少工具往返与上下文 | 生成 runner 输出紧凑 `KEY=VALUE` |

**不适用**：流程还在天天改（`NOT_READY` 护栏会拒绝）、纯知识型、几乎全靠判断的技能。

## 4. 产品形态与核心流程

```
scan → propose → [fill: Agent 补语义 / 插件步骤] → approve --yes [--enable-run-script | --enable-plugins]（人审 + 权限）
     → guard → compile → verify --update-baseline → verify --format jsonl
     → watch / measure → report / bench → ci → package → smoke（干净目录验收）
```

| 阶段 | 产出 | 关键约束 |
|---|---|---|
| `scan` / `propose` | 扫描结果、IR 草案、人审清单 | 每步带原文行号、置信度、推断依据；无法映射的列入 `unmapped` |
| `fill` | 合并补丁后的草案 | 仍是 `PROPOSED`；补丁原文留档；非法补丁不写入 |
| `approve --yes --by` | 正式 IR | 唯一解锁动作；写签发人与时间；**也是 L2/L3 唯一放通入口**，无条件重置三个开关后再按显式参数授予并写 `elevated_by` |
| `guard` | READY / UNSAMPLED / NOT_READY | 形状不稳或改动过频直接拒绝编译 |
| `compile` | runner、精简 SKILL.md、报告、锁文件、IR 终版 | 不覆盖源文件；产物只在 `build/`；二次核对插件目录摘要 |
| `verify` / `watch` | 回归基线、漂移检查 | 缺基线不自动创建；源或 `plugins/` 变更要求重新人审 |
| `measure` / `report` | 实测数据、`report.measured.md` | 明确区分实测与估算；实测报告不进漂移锁 |
| `bench` | 编译前后对照 + `bench-cases.jsonl` | 不调用模型、不编造 token/遵循率；不进漂移锁 |
| `ci` | JSONL cases | 不写技能目录、不自动建基线 |
| `doctor` / `package` / `smoke` | 自检结果、zip + MANIFEST + UPLOAD、干净目录验收 | 逐文件 sha256、CRC 回读、从零跑 32 步含 3 条负例 |

### 六个不可让步的设计

1. **人审对象是 IR，不是生成代码** — JSON 可读、可 diff、可进 Git、可签名。
2. **溯源 + 漂移锁** — 每个 code step 必须带原文行号；源变更后拒绝自动重编译。
3. **机器草案双重门禁** — 顶层 `review_status=PROPOSED` 与每条 `proposal` 元数据同时拦截；`fill` 也不绕过。
4. **特权能力默认关闭且只有一个入口** — `shell`(L2) / `run_script`(L3) / `plugin_*`(L3) 只能在 `approve` 由人用显式参数放通，写 `elevated_by`；补丁、草案、手工改字段都不能自我提权；再加超时 + 无 shell + 路径限定 + 哈希锁定 + 受限命名空间。
5. **诚实口径** — 估算标估算，实测标实测；覆盖率低就公开根因，不放宽门禁凑数字；没有本地 tokenizer 就不给 token 数字；精简版比原文长也如实报告。
6. **可重复验证** — `smoke` 把发行包解压到干净目录从零跑通全链路；「能跑」不是文档里的一句话，而是一条可执行命令。

## 5. 功能规格（1.2.0）

| 模块 | 内容 |
|---|---|
| IR v1.1 | 15 个内置 op；编译期校验溯源、白名单、参数类型与范围、正则、变量声明顺序、保留键、特权开关、源摘要、草案状态 |
| 生成器 | 自包含零依赖 runner；结构化输出；精简版 SKILL.md 始终带可安装 frontmatter；报告；锁文件；回归基线 |
| 语义补全 | `forge fill` + `templates/agent-patch.json` + 两个示例补丁；`proposal.suggestions` 只给 `approve_hint`，不自动放通 |
| 权限签发 | `approve --enable-shell` / `--enable-run-script` / `--enable-plugins`；无条件重置三个开关、`elevated_by == approved_by`、拒绝无谓放通 |
| op 插件 | `plugins/*.py` 静态发现（`ast.literal_eval`，编译期不 import）、`plugin_ops` 白名单、`plugins_sha256` 哈希锁定、受限命名空间（禁 os/subprocess/socket/open）、`watch` 覆盖插件漂移 |
| 稳定性 | `guard` / `measure` / `--record`；输出形状 + git 改动频率；`--force` 豁免绑定源 sha256 且 24h TTL |
| 实测报告 | `measure` 自动写 `report.measured.md`（不进锁），`report` 可单独重生成 |
| 编译前后对照 | `bench`：prompt 体积、确定性、耗时、声明式用例符合度；可选本地 `tiktoken` 精确计数；`bench-cases.jsonl` 供带模型 eval runner 做 A/B；**不测真实 token/遵循率** |
| 批量 | `batch` 只读优先级报告；`--propose` / `--compile-approved` 显式写入并建基线 |
| CI | `ci --strict --format jsonl`；GitHub Actions 3 OS × 2 Python 矩阵、GitLab 双版本、pre-commit；从不写技能目录、从不自动建基线 |
| 可移植性 | `doctor` AST 门禁：反斜杠路径、IO 缺 encoding、`shell=True`、`subprocess.run` 缺 timeout |
| 发行 | `doctor`、`package`、`selftest`（59 条）、`smoke`（32 步）、`LICENSE`、`CHANGELOG.md`、`UPLOAD.md`、MANIFEST |
| 示例 | 四个端到端示例：手工 IR / 自动草案 / CSV+自带脚本+L3+bench / 声明式插件+L3+bench；13 个内置 op + 1 个插件 op 有实例 |
| 回滚 | 首次编译保留原始备份，重复编译不覆盖；`rollback --yes` 恢复 |
| 依赖 | 仅 Python 标准库，>= 3.9，跨平台，生成脚本自包含（插件源码内嵌） |

## 6. 竞品与差异

| 对手 | 形态 | 有 | 无 |
|---|---|---|---|
| SIGIL / 论文原型 | 研究代码 | 自动步骤硬化、收益实测 | 可安装产品、人审 IR、溯源、锁、CI、中文 |
| 社区 `crystallize` | 单脚本 | 快速把散文变代码 | 审计链、回归、漂移、批量、特权分级 |
| 各类 eval 框架 | runner | 触发/遵循率评测 | 编译能力；本工具输出 JSONL 对接它们，不重造 |
| 静态扫描 / 冲突 lint | 装前检查 | 安全风险、词法冲突 | 编译与产物；互补而非竞争 |
| 大厂 Agent Registry | 平台服务 | 企业级目录与治理 | 轻量可安装版；本工具面向中小团队与个人 |

**差异化落点**：可审计 IR + 人审闸门 + 溯源 + 漂移锁 + 回归基线 + 稳定性护栏 + 发行自检，构成一条完整链路，而不是某个单点工具。

## 7. 验收结果

环境：Windows / Python 3.13.14；语法兼容性另用 `ast.parse(feature_version=(3, 9))` 全量校验通过。

### 1.2.0（本轮）

| 项目 | 结果 |
|---|---|
| 测试套件 | **59/59 OK**（45 → 59，新增 14 条覆盖 bench / 插件 / 可移植性 / 编码与路径） |
| 干净目录验收 | `forge smoke .` **32/32 步 PASS**，含 3 条负例；从最终 zip 解压后从零跑通 |
| bench | `data-audit` 4/4 用例、`plugin-audit` 2/2 用例；确定性 3/3 形状稳定；无本地 tokenizer 时明确 `unavailable`；错误期望与路径穿越均被拦截 |
| 插件链路 | 静态发现不 import 源码；无 `--enable-plugins` 签发退出 2；补丁提权退出 2；放通后 `STATUS=WARN`、`RISK_SCORE=4`；`import os` 被 `ImportError` 拦下 |
| 插件漂移 | 改 `plugins/*.py` → `watch` DRIFT(1)、`compile` 因摘要不符退出 2；按字节恢复 → CLEAN(0) |
| 可移植性 | `doctor` 新增 AST 门禁，全项目扫描 0 命中；含 Unicode/空格路径 + 非 UTF-8 locale 运行器实测 |
| 上架自检 | `doctor` **64/64 PASS**；`ci --strict` 4 skill / 8 检查 / 0 失败 |
| 发行包 | CRC + 逐文件 sha256 全一致，包内无 `build/` / `reports/` / `dist/` / `.pyc` |
| 诚实口径 | `plugin-audit` 实测精简版 378 → 513 字符（+36%），`bench` 如实报告负值，未宣称省 prompt |

### 1.1.0（上一轮，回归仍通过）

| 项目 | 结果 |
|---|---|
| 测试套件 | **45/45 OK**（33 → 45，新增 12 条针对本轮缺陷的回归） |
| 干净目录验收 | `forge smoke .` **23/23 步 PASS**，含 2 条负例；从 `dist/` zip 解压后从零跑通 |
| 路径 C 端到端 | `data-audit`：12 个 code 步骤、13 个 op 有实例、`STATUS=BLOCK`、`METRIC_NAMES` 5 项、`VALIDATION_OK=true` |
| L3 权限链路 | 提权补丁退出 2；未放通签发退出 2；`--enable-run-script` 后编译/验证/漂移全通过 |
| 实测报告 | `measure --runs 3` 自动写 `report.measured.md`；`watch` 仍 CLEAN（未进锁） |
| 豁免 TTL | fresh 有效 / 25h 前过期 / 源摘要变更失效，全部符合预期 |
| 静态检查 | 全部 `.py` 语法（含 3.9 兼容）、全部 `.json` 合法、frontmatter 可解析、版本统一 |
| CI 门禁 | `ci --strict` 3 个已编译 skill / 6 项检查 / 0 失败 |

### 1.0.0（上一版，回归仍通过）

| 项目 | 结果 |
|---|---|
| 内置测试套件 | `forge selftest -v` 全绿（IR 门禁、路径穿越、新 op、回归、漂移、护栏、批量、CI、打包、CLI） |
| 双路径等价 | 自动草案签发 vs 手工 IR，生成 runner 输出完全一致（7 个键，`STATUS=NEEDS_CLEANUP`） |
| 新 op 端到端 | `read_text` / `parse_csv` / `pluck` / `count_lines` / `file_exists` / `regex_extract` / `run_script` 全部产出预期值 |
| 草案门禁 | 直接编译草案 → 退出 2；`approve` 不加 `--yes` → 只读、退出 1；`fill` 非法 op → 不写入 |
| 缺基线 | `verify` 返回 `NO_BASELINE` 退出 1，且不创建基线文件 |
| 漂移 | 改源 → `watch` DRIFT(1)、`compile` 被拒；`--force` 放行并写审计；恢复 → CLEAN(0) |
| 护栏 | 人为构造 3 种输出形状 → `NOT_READY` 拒绝编译；`--force` 写豁免留痕 |
| 备份 | 连续两次编译，备份保持首次源文档字节不变 |
| 打包 | zip 回读 CRC + 逐文件 sha256 一致，包内无 `build/` / `reports/` / `.pyc` |
| 静态检查 | 全部 `.py` 语法、全部 `.json` 合法、frontmatter 可解析、版本常量统一 |

**口径**：以上是功能与安全性验证，不是生产基准。token 与遵循率的真实收益需要带模型的外部 eval runner；`measure` 只测脚本侧的耗时、字节与形状一致性，`bench` 只测 prompt 体积与声明式用例符合度。

## 8. 上架 description

见 `SKILL.md` 的 `description` 字段（1.2.0 已加入 bench 编译前后对照、eval 适配、op 插件、plugin 白名单、哈希锁定、enable-plugins 等场景词）。ClawHub 向量检索只索引 description，故用自然语言写满场景词：结晶/编译/压缩、自动填 IR、Agent 补写、run_script、op 插件、token、遵循率、编译前后对照、NOT_READY、批量体检、CI 门禁、漂移、基线、实测报告、smoke、上架自检与打包，以及中英触发词。不堆关键词、不虚假承诺收益数字，并明确写出「本工具不调用模型，不编造 token 或遵循率数字」。`dist/UPLOAD.md` 内有可原样粘贴的版本。

English one-liner for the marketplace listing:

> Compile the deterministic steps in a SKILL.md into a self-contained, zero-dependency Python runner, with a human-reviewed IR, provenance, drift lock, regression baseline, NOT_READY guard and CI gate. v1.2 adds an offline before/after benchmark with an eval-runner adapter and declared, hash-pinned op plugins behind an explicit L3 human grant. It never calls a model and never fabricates token or adherence numbers. Built for skill authors, teams maintaining a skill library, and anyone lowering agent cost or raising procedure adherence before publishing. Not for skills whose procedure still changes every week, pure knowledge skills, or work that is mostly judgment. Trigger phrases: crystallize, compile skill, compact skill, extract steps into scripts, SKILL.md too large, high token usage, inconsistent runs, procedure adherence, skill audit, benchmark, op plugin, doctor, package, skill forge.

## 9. 商业化与壁垒

| 层 | 内容 | 为什么不易被复制 |
|---|---|---|
| Free Skill | 完整 CLI + 15 op + 插件机制 + bench + 门禁 + 文档 | Markdown 会被复制，但工具链需要维护与 59 条测试 |
| 规则集订阅 | 行业特定的归属判定规则、op 模板、插件包、人审清单 | 持续更新的数据，不是单文件 |
| Pro CLI / 服务 | 团队报告、规则签名、批量编译服务、CI SaaS、带模型 eval 对照 | 二进制/服务交付，非纯文本 |
| 企业治理包 | 岗位能力包：owner、版本冻结、更新审计、回滚、到期复核 | 对接内部流程，替换成本高 |

先以免费 Skill 建立使用面，用 `doctor` / `package` 保证分发质量；付费点落在**持续数据与团队能力**，不落在单个 Markdown。

## 10. 里程碑

- **M0（已完成）**：8 op、IR v1、自包含 runner、报告、锁、基线、回滚。
- **M1（已完成）**：`propose` / `approve` 人审闸门、`batch`、`ci`、真实技能库只读报告。
- **M2（1.0.0 完成）**：15 op 与 `run_script` L3、`fill` 语义补丁、`guard` NOT_READY、`measure` 实测接口、`doctor` / `package` / `selftest`、全量缺陷修复与文档对齐。
- **M2.5（1.1.0 完成）**：权限签发收口、`data-audit` 真实示例、生成器 Python 字面量与 BOM 修复、实测报告接入、`smoke` 干净目录验收、跨平台 CI 矩阵、45 条测试。
- **M3（本次 1.2.0 完成）**：`bench` 编译前后对照与 `bench-cases.jsonl` eval 适配；声明式 op 插件（白名单 + 哈希锁定 + 受限命名空间 + L3 人审放通）；`plugin-audit` 第四个端到端示例；可移植性 AST 门禁；59 条测试与 32 步 smoke。
- **M4（下一步）**：真实带模型 eval runner 对照（用 `bench-cases.jsonl` 在多个真实 skill 上出 token / 遵循率表）；跨技能复用编译产物；市场侧 description A/B 触发测试；在 Linux/macOS 实跑 CI 矩阵并回填结果。

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 过度结晶：把还在演化的流程固化 | `guard` NOT_READY + 源漂移护栏 + 要求重新人审 |
| 机器/模型改写引入原文没有的要求 | 强制 `source` 溯源、`fill` 校验、显式 `approve --yes --by` |
| `run_script` 执行任意 Python | 默认关闭、`approve --enable-run-script` 唯一入口、`elevated_by` 留名、目录内 `.py`、无 shell、超时、stdout 截断、风险标 L3 |
| 插件执行任意 Python | 编译期只做 `ast.literal_eval`，从不 import；运行时受限 builtins + 白名单 import（禁 os/subprocess/socket/open）；哈希锁定 + `watch` 漂移；**明确声明不是安全沙箱**，须人工读过源码再签发 |
| Agent 补丁自我提权 | 补丁设三个 `allow_* = true` 一律拒绝；签发时无条件重置开关；无对应步骤的放通请求也拒绝 |
| 强制豁免被长期滥用 | 绑定源 sha256 + 24h TTL + `watch` 提示，过期/源变即失效 |
| 发行包看着完整但跑不起来 | `smoke` 解压到干净目录从零跑 32 步含 3 条负例，作为发布前的硬门槛 |
| 缺基线被当通过 | `verify` 只在显式 `--update-baseline` 时创建；CI 缺基线判 FAIL |
| bench 数字被当模型实测 | 口径写进每份报告与 JSONL；无本地 tokenizer 不给 token 数；`model_eval: NOT_RUN`；精简版变长也如实报告 |
| 跨平台故障只在用户机器上暴露 | `doctor` AST 可移植性门禁 + 3 OS × 2 Python CI 矩阵；仍明确标注 Linux/macOS 本地未实跑 |
| 覆盖率低被当成产品失效 | 探针公开口径；根因是安全策略而非扫描器，不放宽门禁凑数 |
| 发行包混入本机路径/密钥/缓存 | `doctor` 检查、`package` 排除 `build/` 与 `reports/`、逐文件哈希 |
| 版本与文档漂移 | 单一 `version.py` + `doctor` 一致性检查 |
