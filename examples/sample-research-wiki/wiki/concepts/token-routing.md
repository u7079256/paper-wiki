---
type: concept
related_papers: [[paper-alpha]], [[paper-beta]]
---

> SYNTHETIC — illustrative concept synthesizing the two toy papers.

# Token routing(示例 concept)

## 领域现状
把每个 token 路由给少量专家以省算力,是高效序列模型的一条主线。代表:[[paper-alpha]]
(硬 top-k)、[[paper-beta]](软 sparsemax)。

## 前人忽略的问题
两篇都在**离线整段序列**上路由;在线/流式到达的设定都没碰。

## 共存的挑战
负载均衡 vs 精度;稀疏推理的实际加速(Beta 自承软路由没真省在线算力)。

## 主要解决方法族(按方法分类,不按论文分类)
- **硬 top-k 路由** —— [[paper-alpha]]:不可微、需均衡损失。
- **软可微路由** —— [[paper-beta]]:平滑梯度,但推理需全算后剪枝。

## 开放问题 / Gap
**流式逐 token 到达时如何路由**(两篇都列为局限)→ 见 [[streaming-token-routing]]。
