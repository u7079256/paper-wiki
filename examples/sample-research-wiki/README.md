# Sample research wiki (illustrative)

> ⚠️ **Synthetic content.** The "papers" below (*Paper Alpha*, *Paper Beta*) are
> **made up** to show the **structure**, frontmatter, reverse-links, and the
> paper → concept → gap graph — **without** making real-world factual claims. Real
> notes are compiled from real sources under the no-hallucination rule; this folder
> is a shape demo, not a real compilation.

What a finished research wiki looks like:

```
wiki/
├── papers/
│   ├── paper-alpha.md          two toy papers in the same method family,
│   └── paper-beta.md           cross-linked to each other and to the concept
├── concepts/
│   └── token-routing.md        synthesis of ≥2 papers, by method not by paper
└── gaps/
    └── streaming-token-routing.md   a novelty gap seeded from the concept
```

Reverse-link graph (open in Obsidian to see it live):
```
paper-alpha ─┐
             ├─► token-routing ─► streaming-token-routing (gap)
paper-beta  ─┘
```

Read the four files to see: YAML frontmatter per note type, `[[backlinks]]`,
"局限与 Gap" feeding the gap, and the `novelty_verified` field. Then compare with
the schemas in `docs/llm-wiki.protocol.yaml`.
