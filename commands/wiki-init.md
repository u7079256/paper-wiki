---
description: Initialize a freshly bootstrapped LLM Wiki by filling WIKI.md and research.md. Run once.
argument-hint: (interactive; no arguments)
---

Run the paper-wiki `wiki-init` action. This file is a Claude Code slash-command
adapter; the initialization contract is shared with Codex.

## 1. Verify the project and detect the variant

Read the beginning of `WIKI.md`.

- Continue only if the bootstrap TODO banner is present.
- Detect `research` or `course` from `WIKI.md`.
- If the TODO banner is absent, stop and explain that the project is already
  initialized. Do not silently re-run initialization.

`WIKI.md` is the **only authoritative project rules file**. `CLAUDE.md` and
`AGENTS.md` are thin runtime adapters and must remain thin; do not copy project
rules into either file.

## 2. Collect real project information

Ask the user for the missing information. Do not infer or invent it.

For a research wiki, collect:

1. One-sentence research focus.
2. Submission target.
3. Seed works, each with its role and arXiv ID or URL.
4. Dataset(s), if any.
5. Optional Adjacent OK areas, each with a reason.
6. Optional Exclusions, each with a reason and whether it is temporal
   supersession or categorical exclusion.

For a course wiki, collect:

1. Course name and type.
2. Intended use: revision, teaching support, or study.
3. Location of the material, such as a resources archive or folder.
4. Optional document that defines exam/course scope.

## 3. Propose, confirm, then edit

Before writing, show the exact initialization plan and wait for confirmation.
After confirmation:

1. Remove the TODO banner from `WIKI.md`.
2. Replace only the topic/course placeholders and project-specific seed/material
   sections in `WIKI.md`; preserve all reusable rules.
3. Fill every project placeholder in `research.md` and append a dated progress line.
4. Research: fill seed directions and the scope fence; set
   `lifecycle_state: BUILDING`.
5. Course: inventory unpacked materials and optionally draft
   `wiki/exam-scope.md` from the user-designated scope document.

Do **not** edit `CLAUDE.md` or `AGENTS.md` during initialization.

## 4. Optional first ingest

Offer a complete ingest plan and wait for approval before downloads, archive
extraction, mass OCR, or fan-out.

- Research: verify each source identity; use one non-overlapping worker per source.
  A failed or mismatched source is reported and gets no note.
- Course: unpack into a temporary staging directory, remove archive junk such as
  `__MACOSX`, `.DS_Store`, and `._*` there, then append the cleaned source tree to
  `raw/<topic>/` without overwriting existing files. Keep the original archive.
- Born-digital sources may use a faithful HTML/LaTeX path. Scanned or figure-heavy
  material uses the GPU OCR pipeline described by the project docs.
- Credentials come from environment variables and never enter the repository.

Finish by reminding the user that `wiki-init` is one-time and future work uses the
other paper-wiki actions.

## Single-writer rule

Only one runtime may write a workspace at a time. Before switching between Claude
Code and Codex, finish the current task and inspect the working tree. Never launch
initialization or ingest from both runtimes concurrently.
