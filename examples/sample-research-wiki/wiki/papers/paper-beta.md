---
title: "Paper Beta: Soft Differentiable Routing without Hard Top-k (SYNTHETIC EXAMPLE)"
authors: B. Example et al.
venue: arXiv 20xx.xxxxx (illustrative)
tags: [token-routing, differentiable, synthetic-example]
status: compiled
compiled_at: 2026-01-02
source_path: raw/demo/paper-beta.md
---

> SYNTHETIC — made-up content showing structure only.

## 一句话总结
用全可微软路由替代 top-k 硬选择,训练更稳。

## 解决什么问题
[[paper-alpha]] 的 top-$k$ 硬路由不可微、训练有噪;Beta 想要平滑梯度。

## 核心方法
软权重 $w_e=\mathrm{sparsemax}(W_r x)_e$ 对所有专家加权求和,推理时按阈值剪枝近似稀疏。

## 关键实验结果
与 [[paper-alpha]] 同算力下精度 +0.2,收敛步数 −20%。(示意数字)

## 局限与 Gap
软路由推理仍需算全部专家再剪枝,**没有真正省下在线算力**;同样未处理流式。

## 相关工作反链
- [[token-routing]]
- [[paper-alpha]](对照:硬 top-k vs 软 sparsemax)
