# NOT_READY 护栏与实测口径

## 为什么要拒绝编译

结晶是把「流程」冻成代码，并给它配上回归基线、漂移锁和 CI 门禁。如果流程本身还在
天天变，冻结只会让错误流程获得一层虚假的权威：基线会锁住错误输出，CI 会挡住正确的
修改。所以 `compile` 之前会先问一句：**这个形状稳定吗？**

## 护栏用哪些证据

全部本地、无网络、无模型调用：

1. **输出形状稳定性**：`build/fixtures/runs.jsonl` 记录每次 runner 运行的
   `keys`（去掉耗时等元信息）、值摘要、STATUS 与耗时。默认取最近 5 条；
   样本达到 3 条后，若出现多于一种键集合 → `NOT_READY`。
2. **源文档改动频率**：若 skill 目录在 git 仓库内，统计近 7 天 `SKILL.md` 的提交数，
   超过 4 次 → `NOT_READY`。git 不可用则跳过这一项，不误判。
3. **人工豁免**：`--force` 会写 `build/guard-override.json`（含源文档 sha256、
   时间、原因）。豁免只在源文档未变时有效；源一改就自动失效。

| 状态 | 含义 | compile |
|---|---|---|
| `READY` | 采样形状一致 | 放行 |
| `UNSAMPLED` | 样本 < 3 条 | 放行 + GUARD 提示（新技能默认走这条） |
| `NOT_READY` | 形状不稳定或改动过频 | **拒绝，退出 2** |
| `OVERRIDDEN` | 有针对当前源的有效豁免 | 放行 + 明确标注 |

`forge guard <dir> [--min-runs N] [--format json]` 可单独查询状态，不写任何文件。

## 怎么采样

```bash
cd scripts
D=../examples/notes-audit-auto
python forge.py compile "$D"
python forge.py measure "$D" --runs 5 --record     # 写 runs.jsonl
python forge.py verify "$D" --update-baseline --record
python forge.py guard "$D"
```

`--record` 只在显式给出时才写 `runs.jsonl`。CI 的 `verify` 与 `ci` 命令**从不**
写技能目录，避免只读门禁产生副作用。

## measure 实测什么、不实测什么

`forge measure <dir> --runs N` 每次真实启动生成脚本，输出 JSON + Markdown 报告：

| 指标 | 是否实测 | 说明 |
|---|---|---|
| 墙钟时间中位数 / P95 | 是 | 含 Python 启动开销 |
| 输出字节中位数 | 是 | UTF-8 字节数 |
| 输出形状一致性 | 是 | `keys` 集合是否稳定 |
| 成功运行次数 | 是 | returncode=0 且 STATUS≠ERROR |
| 真实 tokenizer token 数 | **否** | 需要具体模型的 tokenizer |
| 模型步骤遵循率 | **否** | 需要带真实模型的 eval runner |

`report.md` 里的「省约 N token」始终是 **12 token/行 的粗估**，报告里已显式标注。
要拿真实数字，把 `forge verify --format jsonl` / `forge ci --format jsonl` 的输出喂给
Skill-Up、skills-evals 或 `evals/evals.json` 这类外部 eval 工具。

## 与回归基线的分工

- `verify` 的基线锁的是**值**：同一输入必须产出同样的 `KEY=VALUE`。
- `guard` 的 runs 锁的是**形状**：不同次运行不应一会儿多一个键、一会儿少一个键。
- `watch` 锁的是**源与产物**：文档或生成文件被改过就报 DRIFT。

三者互不替代。`measure --record` 只影响 `guard`，不会改写 `baseline.json`。

## 1.1.0：豁免 TTL 与实测报告

### 强制豁免不再永久有效

`compile --force` 写的 `build/guard-override.json` 现在带 `forced_at`、`expires_at`、
`ttl_seconds=86400` 和 `source_sha256`。`guard` 只在下面三个条件同时成立时放行：

1. 摘要与当前源 SKILL.md 一致；
2. `forced_at` 可解析、不在未来、且距今不超过 24 小时；
3. 文件内容未被手工破坏。

过期或源一改，豁免自动失效，必须重新 `--force`（重新留痕）。`watch` 在 CLEAN 时会附带
提示当前豁免状态，避免「本地一直能编译、CI 却拒绝」这类静默差异。

### 实测数据接进报告，但不破坏漂移锁

`report.md` 属于 4 个被锁定的编译产物之一，必须保持确定性，因此**不能在 measure 之后
改写它**。1.1.0 的做法是：

- `forge measure <dir> --runs N` 写 `measurement.json` / `.md` 后，自动调用一次 `report`；
- `forge report <dir>` 生成 `build/report.measured.md` = 估算报告全文 + 实测数据表 +
  口径说明；
- 该文件**不在 `LOCKED_ARTIFACTS` 里**，刷新它不会触发 `watch` DRIFT；
- `report.md` 里只有一句静态指针，不引用会变化的时间或数值。

实测表给出请求/成功次数、形状是否稳定、耗时中位数与 P95、输出字节中位数。这些是
**脚本侧**指标；真实 tokenizer token 与模型步骤遵循率依旧要靠外部 eval runner。

### 1.2.0 补上编译前后对照

`forge bench <dir>` 在同一份编译产物上再测：源 SKILL.md 与精简版的字符/词/行/字节、
运行器重复运行确定性、声明式用例符合度，并输出 `build/bench-cases.jsonl`（含两份 prompt
全文）作为带模型 eval runner 的适配输入。它与 `measure` 一样写进 `build/` 但**不进锁**，
`watch` 仍 CLEAN。口径与 fixtures 格式见 `references/benchmark-and-plugins.md`。

三者互不替代：`measure` 采样形状喂给 `guard`，`report` 把实测写成人读报告，
`bench` 做编译前后对照与 eval 适配。都不测真实 token / 遵循率。
