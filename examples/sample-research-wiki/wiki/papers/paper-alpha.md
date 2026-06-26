---
title: "Paper Alpha: Sparse Token Routing for Efficient Sequence Models (SYNTHETIC EXAMPLE)"
authors: A. Example et al.
venue: arXiv 20xx.xxxxx (illustrative)
tags: [token-routing, efficiency, synthetic-example]
status: compiled
compiled_at: 2026-01-01
source_path: raw/demo/paper-alpha.md
---

> SYNTHETIC — made-up content showing structure only.

## 一句话总结
用稀疏路由把 token 分配给少量专家,降算力。

## 解决什么问题
密集序列模型对每个 token 都过全部参数,算力随长度线性涨;前人静态稀疏化损精度。

## 核心方法
每层一个 router $g(x)=\mathrm{softmax}(W_r x)$,取 top-$k$ 专家,负载均衡损失
$\mathcal{L}_{bal}=\alpha\sum_e f_e p_e$。训练端到端。

## 关键实验结果
toy 基准上算力 −40%、精度 −0.3(对比密集 baseline)。(示意数字)

## 局限与 Gap
只在离线整段序列上路由;**流式/在线逐 token 到达时如何路由未解决**。

## 相关工作反链
- [[token-routing]]
- [[paper-beta]](Beta 用可学习而非 top-k 硬路由)
