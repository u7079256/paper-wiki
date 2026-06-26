[English](README.en.md) | **中文** | [项目主页](https://u7079256.github.io/paper-wiki/)

# paper-wiki

把一堆论文或课程材料丢给 Claude，让它逐页读完、写成带引用的笔记、交叉综合出概念图谱——
不是 RAG 那种"查到再拼"，而是**先编译、后查询**的 LLM Wiki。

## LLM Wiki 和 RAG 有什么不同

RAG 每次提问都临时检索文本片段再拼答案，质量取决于切分和召回；LLM Wiki 反过来——
Claude **事先把每篇源文献从头读到尾**（包括附录），写成结构化笔记并标注出处，
再跨源综合出概念条目和研究空白。编译好的 wiki 本身就是产出，跨会话可用，
用 Obsidian 打开就是一张知识图谱。阅读成本只付一次，之后随便查。

## 两种变体，开箱即用

| | **research**（科研） | **course**（课程） |
|---|---|---|
| 源材料 | 论文（arXiv / 网页） | 讲义 / 实验 / 作业 |
| 主笔记层 | `wiki/papers/` | `wiki/lectures/` + `wiki/practice/` |
| 综合层 | `wiki/concepts/` | `wiki/topics/` |
| 特色功能 | `wiki/gaps/`（新颖性分析） | `wiki/exam-scope.md`（考试大纲骨架） |
| 外部检索 | 有（`/wiki-search-latest`、`/wiki-ideate`） | 无 |

research 适合文献调研和 novelty gap 分析（research 变体的 `research.md` 含 scope fence，定义核心聚焦、相邻可纳入、硬排除范围，agent 自动据此筛选）；course 适合复习备考，把几十份讲义压缩成可查的知识库。

## 安装

paper-wiki 是 Claude Code plugin，两条命令装好，不用手动往 `~/.claude/` 里复制任何东西：

```
/plugin marketplace add u7079256/paper-wiki
/plugin install paper-wiki@paper-wiki
```

装完后所有 `/paper-wiki:wiki-*` 命令全局可用。后续更新：`/plugin marketplace update paper-wiki`。

> **遇到 SSH host-key 报错？** plugin 安装走的是 SSH 克隆。如果报
> `No ED25519 host key is known for github.com`，执行一次：
> ```
> ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts
> ```
> 然后重新执行上面的安装命令即可。

> 不想装 plugin 也行——直接 `git clone` 本仓库，用下面的 bootstrap 脚本创建项目。
> 创建出来的项目自带 `/wiki-*` 命令，不依赖 plugin。

## 创建 wiki 项目

bootstrap 脚本会在指定目录生成完整的项目骨架：`raw/` + `wiki/` 两层结构、
`.claude/{commands,agents}`、OCR 脚本，以及根据变体渲染好的 `CLAUDE.md` / `research.md` / `README.md`。

**Windows PowerShell：**
```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Topic my-topic `
    -ProjectName "My Wiki" -Variant research      # 或 -Variant course
```

> 下载的 `.ps1` 第一次跑不了？在当前 shell 里执行一次 `Unblock-File .\scripts\*.ps1`，
> 或者用 `powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_new_wiki.ps1 ...` 绕过。

**macOS / Linux：**
```bash
bash scripts/bootstrap_new_wiki.sh --path ~/my-wiki --topic my-topic \
    --name "My Wiki" --variant research            # 或 --variant course
```

项目创建完成后，在该目录启动 Claude Code，运行 `/wiki-init` 初始化。

### 命令作用域：项目 vs 全局

Claude Code 从两个位置加载命令：

- **项目级** — `<项目>/.claude/commands/`：只在该项目目录内生效。bootstrap 生成的命令属于这一级，
  以 `/wiki-*` 形式调用（文档和教程里用的都是这个形式）。
- **全局级** — 装了 plugin 后，命令以 `/paper-wiki:wiki-*` 形式在任何目录可用。

两者是同一套命令，只是命名空间不同。

## 5 分钟快速上手（不需要 GPU）

不用配 OCR、不用远程服务器——靠 Claude WebFetch 直接读论文 HTML，体验完整流程。

1. 创建临时项目：
   ```powershell
   .\scripts\bootstrap_new_wiki.ps1 -NewPath D:\demo-wiki -Topic demo `
       -ProjectName "Demo" -Variant research
   ```

2. 在新目录启动 Claude Code：
   ```powershell
   cd D:\demo-wiki
   claude
   ```

3. 让 Claude 导入一篇论文（无 OCR 路径）：
   ```
   Import arXiv:1706.03762 the no-OCR way:
   1. WebFetch https://arxiv.org/abs/1706.03762 to confirm the title/authors.
   2. WebFetch the HTML full text (try https://ar5iv.org/abs/1706.03762) and save the
      extracted text to raw/demo/attention-is-all-you-need.md (raw is append-only).
   3. Then run /wiki-compile.
   Follow CLAUDE.md: read the whole thing, cite, never invent.
   ```

4. 查询编译好的 wiki：
   ```
   /teach What is the core contribution and what are the key components?
   ```
   回答只基于已编译的笔记，附带引用；wiki 里没有的内容会明确告知"not in wiki"。

导入 2-3 篇同主题论文后再跑 `/wiki-compile`，Claude 就会自动综合出 `wiki/concepts/` 下的概念条目。

> 不想动手也能看效果：`examples/sample-research-wiki/` 是现成的示例 wiki（标注了是演示数据），
> 展示了双向链接结构——论文笔记、概念条目、研究空白彼此互链。用 Obsidian 打开可以看到图谱。

## 命令速查

| 命令 | 功能 |
|---|---|
| `/wiki-init` | 初始化（仅首次）：填写主题和种子论文（research）/ 解包并清点材料（course） |
| `/wiki-compile` | 读取 `raw/` 中的新材料，编译笔记，综合概念或主题 |
| `/wiki-search-latest <主题>` | （research）搜索最新相关论文 |
| `/wiki-critique <文件>` | 对抗性审查：找漏洞、过度声明、公式错误 |
| `/wiki-ideate <gap>` | （research）发现未试过的组合 |

> 查询 wiki 用 `/teach <问题>`——它会自动读 wiki 笔记、标出处、展开交互式教学，wiki 里没有的内容会明确告知。

> 完整的命令教程见 **[docs/TUTORIAL.md](docs/TUTORIAL.md)**。
> 三个真实场景的完整走读（PhD 生 / 资深研究者 / 长期维护者）见 **[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)**。

## OCR 配置

扫描版 PDF 或图表密集的文献需要 OCR。OCR 在 **GPU 上运行**（本地或远程），不支持纯 CPU。
born-digital 论文可以跳过 OCR，直接走 WebFetch 路径。

- **本地 GPU：** `conda activate mineru; python scripts/mineru_local_ocr.py`
- **远程 GPU（你自己的 SSH 机器）：** 凭据走环境变量，绝不写进仓库：
  ```
  $env:MINERU_REMOTE_HOST = "<你的 GPU 主机>"
  $env:MINERU_REMOTE_USER = "<SSH 用户名>"
  $env:MINERU_REMOTE_PASS = "<密码>"   # 仅存在本地内存，不要提交
  python scripts/mineru_remote_ocr.py
  ```
- **PPTX** 不能直接 OCR，先转 PDF（`soffice --headless --convert-to pdf`）或用 `scripts/extract_pptx.py`（有损）。

> 详细的手把手指南：**[docs/OCR-SETUP.md](docs/OCR-SETUP.md)**。

## 项目结构

```
.claude-plugin/             plugin 元数据（一键安装用）
skills/paper-wiki/SKILL.md  skill 入口（Claude 的行为说明）
scripts/                    bootstrap 脚本 + OCR 脚本 + PPTX 提取 + requirements.txt
commands/                   slash 命令定义
agents/                     sub-agent 定义（wiki-critic / wiki-searcher / wiki-ideator）
templates/{research,course} 各变体的 CLAUDE.md / research.md / README.md 模板
templates/memory/           占位 memory 文件（GPU 服务器信息等）
docs/TUTORIAL.md            命令教程（research + course）
docs/OCR-SETUP.md           OCR 配置指南
docs/METHODOLOGY.md         方法论详解（为什么这样设计）
docs/GOTCHAS.md             踩过的坑（改脚本前务必读）
docs/llm-wiki.protocol.yaml 机器可读的行为规约（LLM 的权威行为定义）
examples/QUICKSTART.md      无 GPU 快速上手
examples/sample-research-wiki/  示例 wiki（展示最终产出的结构）
```

## 安全须知

- 本仓库**不含任何凭据**。OCR 脚本里的 host/user 都是占位符，密码从环境变量读取，
  只放在你本地的 Claude Code memory 里就好。
- `templates/memory/remote-ocr-gpu-server.md.tmpl` 是**模板文件**——填入实际信息后
  不要提交。`.gitignore` 已经屏蔽了常见的敏感路径。

## 许可证

MIT

## 致谢

感谢参与内部测试和早期使用的朋友们，方法论和踩坑记录都来自你们的实战反馈。
