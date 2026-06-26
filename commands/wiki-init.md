---
description: 把刚 bootstrap 出来的 LLM Wiki 项目从模板态过渡到本课题 —— 删模板残留、填本项目内容,可选启动首批入库。Bootstrap 后一次性用。
argument-hint: (无参数,交互式)
---

用户刚用 `bootstrap_new_wiki.ps1` 从 paper-wiki 把本项目脚手架建好。你的任务:把 CLAUDE.md / research.md 从模板态过渡到本项目,然后可选地启动首批入库。**全程每阶段先给方案、等用户确认再执行**,不要一口气跑完。

## 步骤 0:确认是待初始化项目 + 判定变体

Read `CLAUDE.md` 顶部 ~15 行。
- 顶部含 `> 🚧 **TODO` 横幅 → 是新 bootstrap 项目,继续。否则停下问:「这个项目看起来已初始化,真要重跑吗?」
- 标题是 **`# Research Wiki`** → **research 变体**;**`# Course Wiki`** → **course 变体**。按对应分支走。

## 步骤 1:收集本项目信息(用 AskUserQuestion,不许凭印象编造)

**research 变体**:问 ① 课题一句话 ② 投稿目标 ③ 核心种子文献(每篇:工作名 / 角色 / arXiv ID 或 URL)④ 数据集(可选)。
**course 变体**:问 ① 这门课是什么(名称 / 性质)② 目的(复习 / 助教 / 学习)③ 材料在哪(常见:项目根目录有个 `*resources*.zip`,或某文件夹)④ (可选)范围由哪份文档界定(如 `Review lecture`)。

## 步骤 2:把文档落到本项目(先给改动方案,确认后再写)

两变体共通:
1. **删顶部 TODO 横幅**(整段连同后随空行)。
2. **改顶部 topic / 课程描述段**为步骤 1 收集到的内容。
3. 其余**通用规则**(目录约定、多 agent、编译规则、远程 OCR 管线、code repo 规则)**一字不动**。
4. 把 `research.md` 里所有 `_(填)_` 占位替换为真实内容;在「最近讨论过的问题 / 当前进度」追加一条带今天日期的记录。

**research 变体**额外:把 CLAUDE.md 末尾「## 种子方向」与 research.md 的种子表填成本项目种子。
**course 变体**额外:见步骤 3 的解压 + 盘点结果填进 research.md 的「材料清单」。

## 步骤 3:入库(先给完整方案让用户拍板,再执行)

### research 变体
问用户是否现在启动「一篇一个 agent」并行入库。若是:对每篇**带 arXiv ID** 的种子并发 spawn general-purpose agent,严格按 CLAUDE.md 编译规则:
- WebFetch `arxiv.org/abs/<id>` 核对标题与该 paper 对得上;`curl.exe -L -o raw/<topic>/<id>.pdf arxiv.org/pdf/<id>` 下载,Bash `tail -c 30` 验 `%%EOF`
- 逐页读完整篇(Read ≤20 页/次,分次读全含 appendix;Read 渲染 PDF 失败则用 PyMuPDF/pdftotext),写 `wiki/papers/<id>.md`,公式 LaTeX、数字照原文
- **铁律**:禁推断/推测/凭记忆补全;身份不符或下载失败 → 如实报告,不写摘要
- 无 arXiv 的种子(博客/项目页):WebFetch 存 `.md` 入 `raw/<topic>/`,不走 OCR

### course 变体
1. **解压材料**(无歧义可直接做):把 `*resources*.zip` 解压进 `raw/<topic>/`,保留其原有子目录结构;清掉 macOS 垃圾(`__MACOSX/`、`.DS_Store`、`._*`);原 zip 保留。列清单(PDF / PPTX / ipynb 数)。
2. **盘点 + 填 research.md 材料清单**;若有范围文档,读它 → 起草 `wiki/exam-scope.md`(可选)。
3. 给入库方案让用户确认:**PDF** 走远程 GPU OCR;**PPTX** 先远程 `soffice --headless --convert-to pdf` 转 PDF(本地无 soffice 时用 `scripts/extract_pptx.py` 兜底,设 `PYTHONIOENCODING=utf-8`);脚本只抓直接子级 PDF(不递归)→ 先平铺到临时目录。
4. 确认后 OCR → `/wiki-compile` 编译 lecture/topic/practice。

## 步骤 4:远程 OCR 凭据

OCR 配置走环境变量(密码绝不入库):
```
$env:MINERU_REMOTE_HOST = "<host>"; $env:MINERU_REMOTE_USER = "<user>"; $env:MINERU_REMOTE_PASS = "<密码,只存本地 memory>"
python scripts/mineru_remote_ocr.py [input_dir]
```
若本项目 memory 已有 `remote-ocr-gpu-server`(从其他项目拷来),提示用户用其中的值。本项目 OCR namespace = `mineru_<ns>_*`,与其他项目隔离;**别和其他项目同时跑 OCR**。

## 铁律
- 通用规则一字不改;只动 topic/课程段 + 种子/材料 + `_(填)_`。
- 步骤 1 信息必须问到,不凭印象编造。
- 入库严格遵守 CLAUDE.md 编译规则与「禁止」条款。
- 改完后**不要重跑** `/wiki-init`(TODO 已删、占位已填)。
