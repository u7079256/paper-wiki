<p align="center">
  <h1 align="center">📚 paper-wiki</h1>
  <p align="center"><b>丢论文进去，编译出知识图谱。</b></p>
  <p align="center">不是 RAG：Claude Code 或 Codex 逐页读完材料，写成带引用的笔记，再综合出概念与研究空白。</p>
</p>

<p align="center">
  <a href="https://u7079256.github.io/paper-wiki/"><img src="https://img.shields.io/badge/Landing_Page-blue?style=for-the-badge" alt="Landing Page"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Claude_Code_%2B_Codex-Compatible-8A2BE2?style=for-the-badge" alt="Claude Code and Codex compatible">
</p>

<p align="center">
  <a href="README.en.md">English</a> · <b>中文</b> · <a href="https://u7079256.github.io/paper-wiki/">项目主页</a> · <a href="docs/WALKTHROUGH.md">场景走读</a> · <a href="docs/TUTORIAL.md">命令教程</a> · <a href="examples/QUICKSTART.md">5 分钟上手</a>
</p>

---

## ⚡ 安装

### Claude Code

在 Claude Code 中运行：

```text
/plugin marketplace add u7079256/paper-wiki
/plugin install paper-wiki@paper-wiki
```

全局入口是 `/paper-wiki:wiki-*`。更新 marketplace：
`/plugin marketplace update paper-wiki`。

### Codex

Codex 端安装的是 **plugin + skill**，不是把 Claude Code 的 slash commands
原样搬过去。同一套 Codex plugin 可供 Codex app、CLI 和 IDE extension 使用；
下面以可复现的 CLI 路径为主。先确认当前版本提供 plugin 命令：

```powershell
codex plugin --help
```

#### 注册并安装

推荐用 CLI，步骤可复现，也适合远程开发机：

```powershell
codex plugin marketplace add u7079256/paper-wiki
codex plugin add paper-wiki@paper-wiki
codex plugin list
```

也可以先运行第一条命令注册 marketplace，然后启动 `codex`，在 Codex
输入框输入 `/plugins`，切换到 **Paper Wiki** marketplace 并选择安装。安装完成后
请新开一个 Codex task，让新的 skill 清单进入会话。

> [!NOTE]
> `codex plugin ...` 在 PowerShell、Bash 等**终端**里运行；`/plugins` 和
> `$paper-wiki` 则输入到 **Codex task 的对话框**。`$paper-wiki` 不是环境变量，
> 也不是 shell 命令。

#### 在 Codex 中调用

全局 plugin 安装后，显式入口是 `$paper-wiki <action>`：

```text
$paper-wiki init
$paper-wiki compile
$paper-wiki critique wiki/papers/example.md
$paper-wiki teach "这个 wiki 中的方法分成哪几类？"
```

也可以直接用自然语言，例如：“用 paper-wiki 编译当前 wiki 的新材料”。显式写
`$paper-wiki` 更适合第一次使用或需要固定 action 的场景。

bootstrap 生成的项目会自带
`.agents/skills/paper-wiki-project/SKILL.md`，因此即使没有安装全局 plugin，进入
项目后也能这样调用：

```text
$paper-wiki-project wiki-init
$paper-wiki-project wiki-compile
$paper-wiki-project wiki-teach "解释这个概念"
```

#### 更新与验证

```powershell
codex plugin marketplace upgrade paper-wiki
codex plugin add paper-wiki@paper-wiki
codex plugin marketplace list --json
codex plugin list --marketplace paper-wiki --available --json
```

刷新 marketplace 后重新执行 `plugin add`，再新开 task 使用新版 skill。

#### Codex 端如何分发

目前采用 **GitHub marketplace 分发**，还不是 OpenAI curated Plugins Directory
中的官方条目：

```text
GitHub 仓库
  → .agents/plugins/marketplace.json   # 让 Codex 发现 paper-wiki
  → .codex-plugin/plugin.json          # 声明插件元数据和 skills/
  → skills/paper-wiki/SKILL.md         # 提供全局 $paper-wiki
  → $CODEX_HOME/plugins/cache/...      # Codex 管理的本地安装副本
```

用户只需添加一次 GitHub marketplace。仓库是发布源，cache 是 Codex 的内部安装
副本，不应手动修改。`.agents/plugins/marketplace.json` 中的 `source.path` 是
`./`，所以同一个 GitHub 仓库既是 marketplace root，也是 plugin root，不需要再维护
一份 Codex 专用 fork。新版本发布时同步更新版本号并推送仓库；用户执行上面的
`marketplace upgrade` + `plugin add` 即可更新。若以后要让用户无需先添加 GitHub
仓库、直接在公共插件目录中发现，还需要走 OpenAI 的
[plugin 提交流程](https://learn.chatgpt.com/docs/submit-plugins)。Codex plugin 与
marketplace 的官方结构说明见
[Build plugins](https://learn.chatgpt.com/docs/build-plugins)。

如果只想让某个 wiki 自包含，也可以直接
`git clone https://github.com/u7079256/paper-wiki.git`，再用 bootstrap 脚本创建
项目；生成项目不依赖全局 plugin，Claude Code 与 Codex 都能直接使用。

---

## 一套 Wiki，两端操作

bootstrap 会生成一份双端自包含项目：

- `WIKI.md` 是**唯一业务规则权威**，保存 variant、schema、编译规则与禁止事项。
- `CLAUDE.md` 和 `AGENTS.md` 都是薄适配层，只负责把对应 runtime 引向
  `WIKI.md`，不各自复制规则。
- Claude Code 使用 `.claude/commands/`；Codex 使用
  `.agents/skills/paper-wiki-project/SKILL.md`。
- `research.md`、`raw/`、`wiki/` 由两端共享，切换后会看到同一份状态。

> [!IMPORTANT]
> **同一 workspace 只能有一个写入者。** 不要让 Claude Code 与 Codex 同时修改
> 同一个 wiki。切换前先等上一项任务结束，并检查工作树是否有未完成改动。

### 调用映射

Claude Code 的 `/wiki-*` 是 slash commands；它们不会在 Codex 中原样变成
slash commands。Codex 加载的是 skill，所以使用 `$paper-wiki`、
`$paper-wiki-project` 或自然语言点名 action。

| Action | Claude Code 项目内 | Codex 项目内 | 全局 plugin |
|---|---|---|---|
| 初始化 | `/wiki-init` | `$paper-wiki-project wiki-init` | Claude `/paper-wiki:wiki-init`；Codex `$paper-wiki init` |
| 编译 | `/wiki-compile` | `$paper-wiki-project wiki-compile` | Claude `/paper-wiki:wiki-compile`；Codex `$paper-wiki compile` |
| 搜论文 | `/wiki-search-latest <主题>` | `$paper-wiki-project wiki-search-latest <主题>` | Claude `/paper-wiki:wiki-search-latest`；Codex `$paper-wiki search` |
| 审查 | `/wiki-critique <文件>` | `$paper-wiki-project wiki-critique <文件>` | Claude `/paper-wiki:wiki-critique`；Codex `$paper-wiki critique` |
| 构思 | `/wiki-ideate <gap>` | `$paper-wiki-project wiki-ideate <gap>` | Claude `/paper-wiki:wiki-ideate`；Codex `$paper-wiki ideate` |
| 查询/教学 | `/wiki-teach <问题>` | `$paper-wiki-project wiki-teach <问题>` | Claude `/paper-wiki:wiki-teach`；Codex `$paper-wiki teach` |

Codex 也支持自然语言，例如：“按 paper-wiki 的 `wiki-compile` action 编译新材料”。
`wiki-teach` 是 paper-wiki 自带的中立查询 action，不依赖两端另装 `/teach`。

---

## 🧠 LLM Wiki vs RAG

```text
RAG:   提问 → 检索片段 → 拼答案 → 质量取决于切分与召回
Wiki:  源材料 → 逐页通读 → 编译笔记 → 综合概念 → 可持续维护的知识图谱
```

| | RAG | LLM Wiki |
|---|---|---|
| 何时读 | 查询时临时检索 | 编译时完整阅读 |
| 知识形态 | 碎片向量 | 结构化笔记 + 双向链接 |
| 跨源综合 | 弱 | 概念条目 + 研究空白 |
| 可信度 | 可能断章取义 | 每条主张标注出处 |

核心铁律：`raw/` 只读且只追加；`wiki/` 可重写；每条主张都能回溯到实际读过的
源材料；wiki 没有的内容明确说 `not in wiki`，不能拿模型记忆补齐。

---

## 🔄 工作流

```text
wiki-init → 导入材料 → wiki-compile → wiki-critique → wiki-ideate
                                ↓                         ↓
                         wiki-search-latest ←── 覆盖缺口 ─┘
                                ↓
                         wiki-compile → wiki-teach
```

`research` 变体使用 `papers → concepts → gaps`；`course` 变体使用
`lectures + practice → topics`。Scope fence 管边界，生命周期
`BUILDING → ACTIVE → FROZEN` 管扩展节奏。

---

## 🚀 创建双端项目

Windows PowerShell：

```powershell
git clone https://github.com/u7079256/paper-wiki.git
cd paper-wiki
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Topic my-topic `
    -ProjectName "My Wiki" -Variant research   # 或 course
```

macOS / Linux：

```bash
git clone https://github.com/u7079256/paper-wiki.git
cd paper-wiki
bash scripts/bootstrap_new_wiki.sh --path ~/my-wiki --topic my-topic \
    --name "My Wiki" --variant research        # 或 course
```

然后任选一端启动：

```powershell
cd D:\my-wiki
claude   # 随后运行 /wiki-init
# 或
codex    # 随后运行 $paper-wiki-project wiki-init
```

完整无 GPU 示例见 [examples/QUICKSTART.md](examples/QUICKSTART.md)，逐 action
教程见 [docs/TUTORIAL.md](docs/TUTORIAL.md)。

### 更新已有项目

```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Update
```

```bash
bash scripts/bootstrap_new_wiki.sh --path ~/my-wiki --update
```

`-Update` / `--update` 会刷新受管的 Claude commands/agents、Codex 项目 skill、
薄适配层、manifest 和项目内协议文档；**不会覆盖** `WIKI.md`、`research.md`、
项目 `README.md`、`raw/` 或 `wiki/`。旧版 Claude-only 项目首次更新时，会先把
原完整 `CLAUDE.md` 迁移成 `WIKI.md`，再把 `CLAUDE.md` 收薄。若 variant 信息冲突，
脚本会停止，不会猜测。

---

## 🔬 OCR

扫描版、课件和图表密集 PDF 使用本地或远程 GPU OCR；不允许静默降级到 CPU。
Born-digital 论文可走 HTML/LaTeX 的无 OCR 路径。凭据只放环境变量，绝不能进仓库。

详见 [docs/OCR-SETUP.md](docs/OCR-SETUP.md) 与
[docs/GOTCHAS.md](docs/GOTCHAS.md)。

---

## 📁 仓库与生成项目结构

```text
paper-wiki/
├── .claude-plugin/              # Claude Code marketplace 元数据
├── .codex-plugin/               # Codex plugin manifest
├── .agents/plugins/             # Codex marketplace 元数据
├── skills/paper-wiki/           # 全局 plugin skill
├── commands/                    # Claude slash adapters，正文共享 action 契约
├── agents/                      # reviewer/searcher/ideator worker 定义
├── templates/{research,course}/ # WIKI.md + 薄 adapters + 项目 skill
├── scripts/                     # bootstrap、OCR、PPTX 提取
├── docs/                        # protocol、教程与方法论
└── examples/                    # quickstart 与示例 wiki

bootstrapped-project/
├── WIKI.md                      # 唯一业务规则权威
├── CLAUDE.md                    # Claude Code 薄适配层
├── AGENTS.md                    # Codex 薄适配层
├── research.md                  # 两端共享状态
├── .claude/{commands,agents}/   # Claude Code 项目入口
├── .agents/skills/paper-wiki-project/SKILL.md
├── .paper-wiki/                 # manifest + 自包含协议文档
├── raw/                         # 只读源材料
└── wiki/                        # 可维护的编译产物
```

机器契约见 [docs/llm-wiki.protocol.yaml](docs/llm-wiki.protocol.yaml)，当前版本
`llm-wiki/1.1`。

## 📄 许可证

MIT

## 🙏 致谢

- [mattpocock/skills — teach](https://github.com/mattpocock/skills/tree/main/skills/productivity/teach) 启发了交互式教学方法；paper-wiki 现在把查询能力作为自身 `wiki-teach` action 提供。
- 感谢早期使用者把真实项目里的方法论与坑带回这个仓库。
