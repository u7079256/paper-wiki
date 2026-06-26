---
type: gap
seeded_from: [[token-routing]], [[paper-alpha]], [[paper-beta]]
novelty_verified: false   # 示例:待 /wiki-ideate 验证
---

> SYNTHETIC — illustrative gap showing the gap schema + reverse-links.

# Gap:流式 token routing

## 问题陈述
现有 token routing([[paper-alpha]] 硬 top-k、[[paper-beta]] 软 sparsemax)都假设
**整段序列已知**才路由。token **逐个在线到达**(流式推理)时如何即时路由,空白。

## 为什么前人没解决
两篇的 router 都依赖全序列统计/批内归一化;在线设定下未来 token 不可见,均衡与
归一化都要重定义。

## 可能的切入点
- 因果 router(只用历史 token 决策)+ 在线负载均衡;
- 把 [[paper-beta]] 的软权重改成可增量更新的形式。

## 相关文献
- [[paper-alpha]]、[[paper-beta]](均自承流式未解)、[[token-routing]]

> 下一步:`/wiki-critique wiki/gaps/streaming-token-routing` → `/wiki-ideate`。
