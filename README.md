<p align="center">
  <h1 align="center">📚 paper-wiki</h1>
  <p align="center"><b>丢论文进去，编译出知识图谱。</b></p>
  <p align="center">不是 RAG，是 LLM Wiki——Claude 逐页读完每篇论文，写成带引用的笔记，交叉综合出概念和研究空白。</p>
</p>

<p align="center">
  <a href="https://u7079256.github.io/paper-wiki/"><img src="https://img.shields.io/badge/Landing_Page-blue?style=for-the-badge" alt="Landing Page"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" alt="MIT License"></a>
  <a href="https://claude.ai/code"><img src="https://img.shields.io/badge/Claude_Code-Plugin-8A2BE2?style=for-the-badge" alt="Claude Code Plugin"></a>
</p>

<p align="center">
  <a href="README.en.md">English</a> · <b>中文</b> · <a href="https://u7079256.github.io/paper-wiki/">项目主页</a> · <a href="docs/WALKTHROUGH.md">场景走读</a> · <a href="docs/TUTORIAL.md">命令教程</a> · <a href="examples/QUICKSTART.md">5 分钟上手</a>
</p>

---

## ⚡ 一键安装

```
/plugin marketplace add u7079256/paper-wiki
/plugin install paper-wiki@paper-wiki
```

装完后 `/paper-wiki:wiki-*` 命令全局可用。后续更新：`/plugin marketplace update paper-wiki`。

<details>
<summary>💡 遇到 SSH 报错？/ 不想装 plugin？</summary>

**SSH host-key 报错**：plugin 安装走 SSH 克隆。报 `No ED25519 host key is known for github.com` 时执行一次：
```
ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts
```

**不用 plugin**：直接 `git clone` 本仓库，用 bootstrap 脚本创建项目——项目自带 `/wiki-*` 命令，不依赖 plugin。
</details>

---

## 🧠 核心理念：LLM Wiki vs RAG

```
RAG:   提问 → 检索片段 → 拼答案 → 质量取决于切分和召回
Wiki:  源文献 → 逐页通读 → 编译笔记 → 综合概念 → 知识图谱 (一次编译，反复查询)
```

| | RAG | LLM Wiki |
|---|---|---|
| 何时读 | 查询时临时检索 | 编译时一次性通读 |
| 知识形态 | 碎片向量 | 结构化笔记 + 双向链接 |
| 跨源综合 | 无 | 自动生成概念条目 + 研究空白 |
| 可信度 | 拼接可能断章取义 | 每句话标注出处 |

---

## 🔄 工作流一览

```
/wiki-init → 导入论文 → /wiki-compile → /wiki-critique → /wiki-ideate
                                ↓                              ↓
                          /wiki-search-latest ←── 发现缺失覆盖 ──┘
                                ↓
                          /wiki-compile → /teach (深入理解)
```

> **Scope fence** 守住边界：定义核心聚焦、相邻可纳入、硬排除范围，agent 自动据此筛选。
> **Lifecycle** 控制节奏：`BUILDING` → `ACTIVE` → `FROZEN`，wiki 知道什么时候该停。

---

## 📋 两种变体

| | **research**（科研） | **course**（课程） |
|---|---|---|
| 源材料 | 论文（arXiv / 网页） | 讲义 / 实验 / 作业 |
| 主笔记层 | `wiki/papers/` | `wiki/lectures/` + `wiki/practice/` |
| 综合层 | `wiki/concepts/` | `wiki/topics/` |
| 特色功能 | `wiki/gaps/` + `/wiki-ideate` | `wiki/exam-scope.md` |
| 外部检索 | `/wiki-search-latest` | — |
| Scope fence | ✅ | — |

---

## 🛠️ 命令速查

| 命令 | 功能 |
|---|---|
| `/wiki-init` | 初始化：填写主题、种子论文、scope fence |
| `/wiki-compile` | 编译 `raw/` 中的新材料 → 笔记 → 概念 → gap |
| `/wiki-search-latest <主题>` | 搜索最新论文（research） |
| `/wiki-critique <文件>` | 对抗性审查：找漏洞、过度声明、公式错误 |
| `/wiki-ideate <gap>` | 发现未试过的方法-问题组合（research） |
| `/teach <问题>` | 查询 + 交互式教学：跨论文对比、gap 状态汇总 |

---

## 🚀 5 分钟快速上手（不需要 GPU）

```powershell
# 1. 创建项目
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\demo-wiki -Topic demo `
    -ProjectName "Demo" -Variant research

# 2. 进入项目
cd D:\demo-wiki && claude

# 3. 在 Claude Code 中导入一篇论文 + 编译 + 查询
```

完整步骤见 **[examples/QUICKSTART.md](examples/QUICKSTART.md)**。
示例 wiki 产出见 **[examples/sample-research-wiki/](examples/sample-research-wiki/)**。

---

## 📖 使用场景走读

三个研究者的完整使用故事——从创建到投稿：

| 场景 | 角色 | 周期 | 重点 |
|---|---|---|---|
| A | PhD 生，新方向 | 8 周 | scope fence 延迟填写、ideate 发现方向 |
| B | 资深研究者 | 4 周 | 闪电验证 + 快速写作 |
| C | 长期维护者 | 6 周 | 跨论文复用、`--Update` 更新命令 |

详见 **[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)**。

---

<details>
<summary>🏗️ 创建 wiki 项目（bootstrap 详情）</summary>

bootstrap 脚本生成完整项目骨架：`raw/` + `wiki/` 两层结构、`.claude/{commands,agents}`、OCR 脚本、变体模板。

**Windows PowerShell：**
```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Topic my-topic `
    -ProjectName "My Wiki" -Variant research      # 或 -Variant course
```

**macOS / Linux：**
```bash
bash scripts/bootstrap_new_wiki.sh --path ~/my-wiki --topic my-topic \
    --name "My Wiki" --variant research            # 或 --variant course
```

项目创建完成后，在该目录启动 Claude Code，运行 `/wiki-init`。

**更新已有项目的命令/agent**（不碰 CLAUDE.md 和 research.md）：
```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Update
```

**命令作用域**：bootstrap 装的是项目级命令（`/wiki-*`）；plugin 装的是全局命令（`/paper-wiki:wiki-*`）。同一套命令，命名空间不同。
</details>

<details>
<summary>🔬 OCR 配置（扫描版 / 图表密集 PDF）</summary>

OCR 在 **GPU 上运行**（本地或远程），不支持纯 CPU。born-digital 论文可跳过 OCR 走 WebFetch 路径。

- **本地 GPU：** `conda activate mineru; python scripts/mineru_local_ocr.py`
- **远程 GPU：** 凭据走环境变量，绝不写进仓库：
  ```
  $env:MINERU_REMOTE_HOST = "<你的 GPU 主机>"
  $env:MINERU_REMOTE_USER = "<SSH 用户名>"
  $env:MINERU_REMOTE_PASS = "<密码>"
  python scripts/mineru_remote_ocr.py
  ```
- **PPTX**：先转 PDF（`soffice --headless --convert-to pdf`）或 `scripts/extract_pptx.py`（有损）。

详见 **[docs/OCR-SETUP.md](docs/OCR-SETUP.md)**。
</details>

<details>
<summary>🔒 安全须知</summary>

- 本仓库**不含任何凭据**。OCR 脚本里的 host/user 都是占位符，密码从环境变量读取。
- `templates/memory/remote-ocr-gpu-server.md.tmpl` 是模板文件——填入实际信息后不要提交。
</details>

<details>
<summary>📁 项目结构</summary>

```
.claude-plugin/             plugin 元数据（一键安装用）
skills/paper-wiki/SKILL.md  skill 入口（Claude 的行为说明）
scripts/                    bootstrap (.ps1 + .sh) + OCR + PPTX 提取
commands/                   slash 命令定义
agents/                     sub-agent（wiki-critic / wiki-searcher / wiki-ideator）
templates/{research,course} 各变体的 CLAUDE.md / research.md / README.md 模板
docs/                       TUTORIAL / WALKTHROUGH / OCR-SETUP / METHODOLOGY / GOTCHAS
examples/                   QUICKSTART + 示例 wiki
```
</details>

---

## 📄 许可证

MIT

## 🙏 致谢

感谢参与内部测试和早期使用的朋友们，方法论和踩坑记录都来自你们的实战反馈。
