# 使用场景走读 — 三个研究者的 paper-wiki 之旅

> 这不是命令手册（那是 [TUTORIAL.md](TUTORIAL.md)），而是三个真实场景的完整故事：
> 一个 PhD 生从零起步、一个资深研究者快速验证想法、一个长期维护者跨论文复用 wiki。
> 每个场景展示**你会做什么、看到什么、得到什么**。

---

## 场景 A：PhD 生 — 8 周，从"导师的一句话"到投稿

### 背景
二年级博士生，导师说"看看 3DGS 和 diffusion 做 avatar 的可能性"。
读过 3-4 篇论文，对领域没有系统认知。

---

### 第 1 周：起步

**创建项目：**
```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\avatar-wiki -Topic avatar-3dgs `
    -ProjectName "3DGS Avatar Survey" -Variant research
cd D:\avatar-wiki
claude
```

**初始化（`/wiki-init`）：**
Claude 会依次问：
1. 研究方向 → "基于 3DGS 的 avatar 合成"
2. 种子论文 → 她给出 3 篇：GaussianAvatars, SplattingAvatar, HUGS
3. 投稿目标 → "CVPR 2026"
4. 相邻可纳入区域 → **留空**（她还不确定边界在哪）
5. 排除范围 → **留空**（同上）

> **为什么留空没问题？** scope fence 是可选的。wiki-init 会说"不确定可留空，
> 第一轮 compile 之后概念主题出来了再填"。

Claude 自动下载 3 篇种子论文，跑 OCR（或走 HTML 路径），写入 `raw/`。

**首次编译（`/wiki-compile`）：**
输出：
```
Scanned: 3    New: gaussianavatar, splattingavatar, hugs    Skipped: 0
New concepts: 1 (gaussian-avatar-deformation)    New gaps: 0
Tip: scope fence 的 Adjacent OK 和 Exclusions 还是空的。
现在概念主题已经出来了，可以考虑在 research.md 里填上，帮助后续搜索过滤。
```

**A得到了什么：**
- `wiki/papers/` 下 3 份结构化笔记（每篇的方法、贡献、局限一目了然）
- `wiki/concepts/gaussian-avatar-deformation.md`（跨 3 篇论文的方法族综合）
- 一个提示：该填 scope fence 了

---

### 第 2-3 周：扩展

**搜索更多论文（`/wiki-search-latest avatar 3DGS`）：**
搜索 agent 返回 8 篇候选论文的表格：

| # | 论文 | 年份 | 相关度 | 备注 |
|---|---|---|---|---|
| 1 | GaussianHead | 2024 | 高 | head-only, 3DGS |
| 2 | MonoGaussianAvatar | 2024 | 高 | monocular, deformation |
| 3 | DreamGaussian | 2024 | 中 | text-to-3D, 非 avatar 特定 |
| ... | ... | ... | ... | ... |
| 7 | NeRFBlendShape | 2023 | 低 | NeRF-based |

> 如果A之前填了 scope fence 把 NeRF 排除，第 7 篇会标 `[FENCE]` 且不在推荐列表里。

A选了前 5 篇导入，跑了两轮 `/wiki-compile`。wiki 长到 8 篇论文、3 个概念、1 个 gap。

**现在填 scope fence：**
A编辑 `research.md`：
```markdown
### 核心焦点
基于 3DGS 的单图/少图上半身 avatar 合成与动画

### 相邻可纳入
- head-only avatar: 子问题，共享 baseline 和 deformation 方法
- video generation for faces: 最新 SOTA 趋同，可能有可借鉴的方法

### 排除范围
- NeRF-based avatar methods: 该任务已被 3DGS 取代（历史引用可以，不导入新工作）
- motion generation: 不同输出模态（生成动作序列，不是渲染图像）
- full-body motion capture: 不同输入假设（需要多视角 + 惯性传感器）
```

---

### 第 4 周：形成自己的想法

wiki 有 12 篇论文、5 个概念、2 个 gap。A开始觉得有些方向可以做。

**用 `/teach` 深入理解：**
```
/teach 帮我对比一下 GaussianAvatars 和 SplattingAvatar 在 deformation 方法上的区别
```
Teach 输出一张对比表格：

| 维度 | GaussianAvatars | SplattingAvatar |
|---|---|---|
| 变形方式 | FLAME mesh binding | learned UV deformation |
| 表情控制 | blend shapes | neural texture |
| 实时性 | 30fps | 15fps |
| 局限 | 依赖 FLAME 拓扑 | UV 展开有接缝 |

（每个单元格引用对应 wiki 笔记的具体章节）

```
/teach 为什么 UV-based 方法会有接缝问题？推导一下
```
Teach 展开交互式教学：从 UV 映射的数学定义讲起，解释 chart boundary 不连续性，
引用 `wiki/papers/splattingavatar.md § 局限` 和 `wiki/concepts/uv-deformation.md § 已知问题`。

---

### 第 5 周：探索研究机会

**运行 `/wiki-ideate`（exploratory 模式，不带参数）：**

Ideator 扫描全部 5 个概念和 2 个 gap，构建方法族 × 问题矩阵：

```
### Method landscape
| 方法族 | 覆盖 | 未覆盖 |
|---|---|---|
| FLAME-binding | 表情、姿态 | 服装变形、发丝 |
| UV deformation | 纹理映射 | 接缝、时序一致性 |
| feed-forward prediction | 单帧推理速度 | 多帧一致性 |
| diffusion-based refinement | 细节质量 | 实时性 |

### Untried combinations
#### Hypothesis 1: diffusion-guided Gaussian initialization
- Combines: diffusion refinement from [[concept-diffusion-avatar]] + FLAME binding from [[concept-gaussian-deformation]]
- Why it might work: diffusion 生成高质量初始化，3DGS 保证实时渲染
- Why it might NOT work: diffusion 的随机性和 3DGS 的确定性可能冲突
- Web search: "diffusion gaussian avatar initialization" → Untried

#### Hypothesis 2: semantic-aware UV stitching
- ...

### Self-assessment
- Conservatism check: Hypothesis 1 的 "可能冲突" 需要更仔细评估——DreamGaussian 已经证明 diffusion+3DGS 可以协作
- Coverage check: 没有考虑 audio-driven 方向的方法族，这可能是一个盲区
```

A看到 Hypothesis 1 没人做过，决定深入。

**用 `/wiki-ideate wiki/gaps/diffusion-gaussian-init.md`（gap-focused 模式）：**

针对这个具体 gap 做深度分析，搜索最近 12 个月的工作，确认这个组合确实是空白。

---

### 第 6 周：锁定方向

```
# 在 research.md 中设置
lifecycle_state: ACTIVE
```

Wiki 进入 ACTIVE 状态——不再主动扩展，只有在 ideator 发现缺失覆盖时才按需添加。

**最后一次 critique（`/wiki-critique wiki/gaps/diffusion-gaussian-init.md`）：**
Critic 找出 gap 描述中的弱点："你说 'no one has combined' 但 DreamGaussian 在 text-to-3D
场景做了类似的事——你需要限定到 avatar-specific task"。A修正 gap 描述。

---

### 第 7 周：写论文

```
lifecycle_state: FROZEN
```

Wiki 冻结。

```
/teach 帮我按概念分组整理 related work，列出每篇论文和本工作的关系
```

Teach 输出分组的论文列表，每篇一句话定位。A用这个作为 related work 的骨架。

---

### 第 8 周：reviewer 反馈

Reviewer 说："你应该讨论 HeadGAP (ECCV 2024) 和 GART (NeurIPS 2024)。"

```
# 重新打开 wiki
lifecycle_state: BUILDING
```

导入这 2 篇论文 → `/wiki-compile` → `/teach` 理解新论文 → 更新 related work → 重新冻结。

> **如果命令过期了**（比如半年前 bootstrap 的项目还用旧版 wiki-verify-novelty）：
> ```powershell
> .\scripts\bootstrap_new_wiki.ps1 -NewPath D:\avatar-wiki -Update
> # 只更新 commands/ 和 agents/，不碰 CLAUDE.md、research.md 和 wiki 内容
> ```

---

## 场景 B：资深研究者 — 4 周快速验证

### 背景
博后，已经在 avatar 领域发了 3 篇论文。有一个明确想法：
"用 feed-forward Gaussian prediction + semantic-aware deformation 做实时 avatar relighting"。
需要快速确认这个方向没人做过，找到最近的 baseline，写 related work。

---

### 第 1 周（Day 1-2）：闪电初始化

```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\relighting-wiki -Topic avatar-relighting `
    -ProjectName "GS Relighting" -Variant research
```

**`/wiki-init`** — B不需要留空 scope fence，他很清楚边界：

- 种子论文：8 篇（他自己的 3 篇 + 5 篇竞争者）
- Adjacent OK: "inverse rendering: 共享 relighting 物理模型"，"image-based relighting: 2D baseline"
- Exclusions: "NeRF relighting: 3DGS 已取代"，"outdoor scene relighting: 不同光照模型"

**Day 1 compile 完成**：8 篇论文、4 个概念、2 个 gap。

**Day 2 立即 ideate（`/wiki-ideate wiki/gaps/realtime-gs-relighting.md`）：**

因为种子论文质量高、scope 清晰，ideator 产出精准：
- 3 个假设，其中 1 个标记"Partially tried"（有人做了静态版本但没有实时）
- 确认"feed-forward + semantic deformation + relighting"的完整组合无人尝试
- 建议补充 2 篇关于 PBR material estimation 的论文

B在 gap frontmatter 里设 `novelty_verified: true`，心里有底了。

---

### 第 2 周：精准补充

根据 ideator 建议，`/wiki-search-latest PBR material estimation 3DGS` → 导入 3 篇 → compile。

Wiki 长到 11 篇论文、6 个概念、3 个 gap。他觉得够了：
```
lifecycle_state: ACTIVE
```

---

### 第 3 周：写作

```
lifecycle_state: FROZEN
```

```
/teach 对比 RelightableGaussian 和 GS-LRM 在 material estimation 上的方法，给我一张表
```

| | RelightableGaussian | GS-LRM |
|---|---|---|
| Material model | per-Gaussian PBR | global SH + learned residual |
| 训练数据 | multi-view + 已知光照 | 单图 + 预训练 |
| 实时渲染 | 是 | 否（需要 MLP forward） |
| 本工作关系 | 最近的 baseline | material 参数化可借鉴 |

```
/teach 帮我按概念分组整理 related work
```

→ 直接产出 related work 骨架，每篇论文一句话定位。

---

### 第 4 周：Reviews

Reviewer: "你应该和 TensoIR 比较。"

```
lifecycle_state: BUILDING
```
导入 TensoIR → compile → teach 理解 → 加一段 related work → 重新冻结。
全程 30 分钟。

---

## 场景 C：长期维护者 — 跨论文复用 wiki

### 背景
6 个月前建了一个 avatar animation wiki（50 篇论文、8 个概念、4 个 gap），
第一篇论文已经发了。现在她要做第二篇：从 animation 延伸到 avatar interaction。

---

### 第 1 周：重新打开

```
lifecycle_state: BUILDING
```

添加 5 篇 interaction 相关的新种子论文 → `/wiki-compile`。

输出：
```
Scanned: 5    New: interactavatar, handavatar, ...    Skipped: 0
New concepts: 2 (avatar-interaction, hand-object-contact)    New gaps: 1
```

**更新 scope fence**（原来没有 interaction 相关的条目）：
```markdown
### 相邻可纳入
- hand-object interaction: 子问题，avatar interaction 的核心组件
- (原有) head-only avatar: ...

### 排除范围
- (原有) NeRF-based: ...
- (新增) robot manipulation: 不同任务，虽然也涉及 hand-object contact
```

---

### 第 2 周：编译 + 发现

再搜 10 篇 → compile。wiki 长到 65 篇。

`/wiki-critique wiki/concepts/avatar-interaction.md` → critic 发现：
"method families 小节里把 physics-based 和 data-driven 并列，但没有说明两者的适用场景差异。"
A修正了概念笔记。

---

### 第 3 周：Ideate 的噪声问题

`/wiki-ideate`（exploratory）

方法-问题矩阵 10 × 6，但大量旧 animation 方向的组合。因为 scope fence 守住了边界，
interaction 方向的矩阵是干净的；animation 方向的矩阵因为文献太多反而比较杂。

> **这就是 scope fence 作为 ideation 放大器的实际效果**——
> 新方向（interaction）因为论文精选所以组合精准；
> 旧方向（animation）因为 50 篇积累所以需要更强的过滤。

A用 gap-focused 模式 `/wiki-ideate wiki/gaps/hand-avatar-contact.md`，
只看 interaction 方向的组合，质量高很多。

---

### 第 4 周：Teach 深入

```
/teach 解释一下 physics-based contact modeling 和 data-driven 方法各自的数学基础，
给我一张对比表，重点是哪些假设会影响 avatar 场景的适用性
```

Teach 输出详细对比，引用 3 篇论文的具体方法章节。

---

### 第 5 周：发现命令过期了

项目是 6 个月前 bootstrap 的，`.claude/commands/` 里还有旧的 `wiki-verify-novelty.md`，
没有 `wiki-ideate.md`，也没有 `wiki-ask.md` 的 redirect。

```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\avatar-wiki -Update
```
```
Updated 6 commands and 3 agents from paper-wiki.
```

命令更新完成，CLAUDE.md 和 research.md 不受影响。

---

### 第 6 周：冻结，投稿

```
lifecycle_state: FROZEN
/teach 帮我整理这次新增的 interaction 方向的 related work
```

第二篇论文的 related work 骨架 15 分钟搞定——因为所有论文都已经编译、
概念已经综合、gap 已经验证过了。

---

## 一张图总结三个场景的时间线

```
          wk1    wk2    wk3    wk4    wk5    wk6    wk7    wk8
PhD 生     init   expand expand  idea   ideate narrow write  review
资深研究者  init+  fill   write  review
           ideate
长期维护者  reopen compile ideate teach  update freeze
```

核心规律：
- **init + compile** 是所有人的起点
- **ideate** 出现在论文积累到位之后（PhD 第 5 周，资深第 1 天，长期第 3 周）
- **teach** 贯穿全程但在写作阶段最密集
- **scope fence** 越早填越好，但不确定就留空——第一轮 compile 后再填也完全可以
- **lifecycle** 跟着研究节奏走：BUILDING → ACTIVE → FROZEN → 需要时重新 BUILDING
