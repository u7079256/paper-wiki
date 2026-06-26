---
description: 只基于已编译的 wiki 回答问题 —— 只读、不改 wiki、不用通用知识或联网补全。
argument-hint: <你的问题>
---

用户要在本 wiki 上做一次**只读查询**。问题:`$ARGUMENTS`

## 规则(严格遵守)

1. **先读再答**:先读 `research.md`,再用 Grep / Glob 在 `wiki/` 下相关目录(research 变体:`papers/`·`concepts/`·`gaps/`;course 变体:`lectures/`·`topics/`·`practice/`·`exam-scope.md`)定位与问题相关的文件并 Read;文件里的 `[[...]]` 反链可顺藤摸瓜读相关条目。读完再回答。
2. **只用 wiki**:回答只基于 wiki 文件内容。每个关键论断都标出处(`wiki/.../xxx.md`,有行号/页码更好)。
3. **不补全**:wiki 里没有的,明说「wiki 里没有」——**不要用通用知识、不要联网补**。research 变体若需新文献,建议跑 `/wiki-search-latest`。
4. **只读不写**:不修改、不新建任何 wiki 文件,除非用户明确说「写进 wiki」。
5. **快照提醒**:wiki 是按 `compiled_at` 编译的快照;问题涉及「最新 / 现状」时,提醒可能已过时。
6. `$ARGUMENTS` 为空时,请用户先给出要查询的问题。

## 输出

- 直接回答,关键论断附 wiki 出处。
- 末尾列「相关 wiki 条目」便于跳转(`[[...]]`)。
- 若发现 wiki 内部矛盾或可疑之处,如实指出,不替 wiki 圆场。
