# 上架信息 · skill-forge 1.2.0

- 名称：`skill-forge`
- 版本：1.2.0（2026-11-10）
- 许可：MIT
- 风险等级：L1（默认只读；开启 shell=L2，开启 run_script / plugin_* =L3）
- 依赖：Python >= 3.9，仅标准库，无网络调用
- 包内文件：67 个，合计 389154 字节
- ZIP SHA256：`39adaeadbd0d463c67f0adac0f92f4b5bdf649eddae1f415d3ac8af513c20981`

## 一句话

把 SKILL.md 里确定性的重复步骤编译成自包含脚本，让模型只保留判断；带人审闸门、回归基线、漂移锁和 CI 门禁。

## description（原样粘贴）

把 SKILL.md 里确定性的、每次重复执行的步骤「结晶」成可执行脚本，让大模型只保留真正需要判断的部分。 很多技能把固定流程写成散文：统计文件数、跑命令、读配置或 CSV、按阈值判断、校验结果，模型每调用一次就重新解释一遍，容易跳步、乱序、输出忽长忽短，还多烧 token 与工具往返。 本技能提供可审计的编译流水线：静态扫描候选并给原文行号 → 自动推断带置信度与溯源的 IR 草案 → Agent 用补丁补写语义步骤 → 人工签发（未签发草案被门禁拦截；shell、run_script、op 插件只能在签发时用显式参数放通并留名）→ 生成自包含零依赖 Python 运行器与带完整 frontmatter 的精简 SKILL.md → 建回归基线、检测源与插件漂移、按输出形状判断是否 NOT_READY。 IR v1.1 有 15 个内置 op（计数、匹配、收集、读文本/JSON、解析带 BOM 的 CSV、哈希、文件存在性、正则与列提取、阈值决策，以及需放通的 shell 与 run_script）。v1.2 增加声明式 op 插件：plugins/*.py 静态解析、哈希锁定、受限命名空间（禁 os/subprocess/socket）、L3 人审放通，插件改动即触发 DRIFT。 还能用 forge bench 做编译前后离线对照：prompt 体积、运行器确定性、耗时、声明式用例符合度，并产出 bench-cases.jsonl 供带模型的 eval runner 做真实 token／遵循率 A/B；**本工具不调用模型，不编造 token 或遵循率数字**。另有实测报告、技能库批量体检、CI 门禁（3 OS × 2 Python）、doctor 自检、package 打包与 smoke 干净目录一键验收。 全程默认只读、禁网、禁 shell、禁执行技能内脚本与插件；产物写进 build/ 不覆盖源文件，源变更要求重新人审，缺基线不会偷偷创建，强制豁免 24 小时失效。 适合维护技能库的开发者、压低 Agent 成本或提升流程遵循率的团队、上架前做压缩与回归验证的 Skill 作者。不适合形状未稳、纯知识型或几乎全靠判断的技能。 触发词：结晶、编译 skill、压缩 skill、把步骤抽成脚本、自动填 IR、IR 草案、Agent 补 IR、run_script、enable-run-script、op 插件、plugin 白名单、插件哈希锁定、enable-plugins、CSV 审计、SKILL.md 太大、token 太高、每次跑结果不一样、步骤遵循率、编译前后对照、bench、eval 适配、NOT_READY、形状稳定性、实测报告、干净目录验收、批量扫描技能库、技能库体检、CI 门禁、漂移检测、回归基线、上架自检、打包技能、smoke、skill forge、crystallize、compile skill、skill compaction、skill audit、doctor、package。

## 建议标签

`developer-tools` `agent-skills` `skill-authoring` `optimization` `static-analysis` `ci` `python` `chinese`

## 安装后自检

```bash
cd skill-forge/scripts
python forge.py --version
python forge.py doctor .             # 64 项上架自检（含可移植性门禁）
python forge.py selftest .           # 59 条内置回归
python forge.py smoke . --no-zip     # 32 步干净目录端到端验收（推荐）

# 路径 A：自动草案 → 人审 → 编译（最简）
python forge.py propose ../examples/notes-audit-auto
python forge.py approve ../examples/notes-audit-auto --yes --by <你的名字>
python forge.py compile ../examples/notes-audit-auto
python forge.py verify ../examples/notes-audit-auto --update-baseline
python forge.py verify ../examples/notes-audit-auto --format jsonl
python forge.py watch ../examples/notes-audit-auto

# 路径 C：CSV + 技能自带脚本 + L3 显式放通 + 实测 + 编译前后对照
python forge.py propose ../examples/data-audit
python forge.py fill ../examples/data-audit --patch ../examples/data-audit/agent-patch.json --by <来源>
python forge.py approve ../examples/data-audit --yes --by <你的名字> --enable-run-script
python forge.py compile ../examples/data-audit
python forge.py verify ../examples/data-audit --update-baseline
python forge.py measure ../examples/data-audit --runs 3
python forge.py bench ../examples/data-audit --runs 3
python forge.py watch ../examples/data-audit

# 路径 D：声明式 op 插件 + 哈希锁定 + 插件 L3 显式放通
python forge.py propose ../examples/plugin-audit
python forge.py fill ../examples/plugin-audit --patch ../examples/plugin-audit/agent-patch.json --by <来源>
python forge.py approve ../examples/plugin-audit --yes --by <你的名字> --enable-plugins
python forge.py compile ../examples/plugin-audit
python forge.py verify ../examples/plugin-audit --update-baseline
python forge.py bench ../examples/plugin-audit --runs 3
python forge.py watch ../examples/plugin-audit
```

## 校验包完整性

`MANIFEST.json` 内每个文件有 sha256；解压后逐一比对即可。包内不含 `build/` 与 `reports/`，示例产物由上述命令现场生成。

若发行包是从 marketplace 下载后用 `forge smoke .`（不带 `--no-zip`）验收，会解压 `dist/` 里的最新 zip 到干净临时目录从零跑完整链路，含三条负例。`forge bench` 只做离线对照，不调用模型、不报告真实 token 与遵循率；`build/bench-cases.jsonl` 是接外部带模型 eval runner 的适配输入。

