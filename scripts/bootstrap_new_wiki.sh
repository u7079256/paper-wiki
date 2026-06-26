#!/usr/bin/env bash
# Bootstrap a new LLM-Wiki project from the paper-wiki skill (macOS/Linux).
# Mirror of bootstrap_new_wiki.ps1. Templates are read/written as UTF-8.
#
# Usage:
#   bash scripts/bootstrap_new_wiki.sh --path ~/my-wiki --topic my-topic \
#        --name "My Wiki" --variant research          # or --variant course
#   bash scripts/bootstrap_new_wiki.sh --path ~/my-wiki --update
set -euo pipefail

NEW_PATH=""; TOPIC=""; NAME=""; VARIANT="research"; SKILL_ROOT=""; UPDATE=false
while [ $# -gt 0 ]; do
  case "$1" in
    --path)       NEW_PATH="$2"; shift 2;;
    --topic)      TOPIC="$2"; shift 2;;
    --name)       NAME="$2"; shift 2;;
    --variant)    VARIANT="$2"; shift 2;;
    --skill-root) SKILL_ROOT="$2"; shift 2;;
    --update)     UPDATE=true; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$NEW_PATH" ] || { echo "required: --path" >&2; exit 2; }
case "$VARIANT" in research|course) ;; *) echo "--variant must be research|course" >&2; exit 2;; esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -n "$SKILL_ROOT" ] || SKILL_ROOT="$(dirname "$SCRIPT_DIR")"

# variant-aware command / agent lists (used by both create and update)
if [ "$VARIANT" = research ]; then
  CMDS=(wiki-init wiki-compile wiki-search-latest wiki-critique wiki-ideate)
  AGENTS=(wiki-searcher wiki-critic wiki-ideator)
else
  CMDS=(wiki-init wiki-compile wiki-critique)
  AGENTS=(wiki-critic)
fi

# update mode: re-copy commands and agents only
if $UPDATE; then
  if [ ! -d "$NEW_PATH/.claude/commands" ]; then
    echo "This does not look like a paper-wiki project. Run without --update to create a new project." >&2
    exit 1
  fi
  mkdir -p "$NEW_PATH/.claude/agents"
  for c in "${CMDS[@]}";   do cp "$SKILL_ROOT/commands/$c.md" "$NEW_PATH/.claude/commands/"; done
  for a in "${AGENTS[@]}"; do cp "$SKILL_ROOT/agents/$a.md"   "$NEW_PATH/.claude/agents/";   done
  echo "Updated ${#CMDS[@]} commands and ${#AGENTS[@]} agents from paper-wiki."
  exit 0
fi

# non-update mode: Topic is required
[ -n "$TOPIC" ] || { echo "required: --path and --topic" >&2; exit 2; }
[ -n "$NAME" ] || NAME="$TOPIC"
NS="$(printf '%s' "$TOPIC" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]\+/_/g; s/^_//; s/_$//')"
DATE="$(date +%Y-%m-%d)"

echo "Skill root : $SKILL_ROOT"
echo "New project: $NEW_PATH"
echo "Variant    : $VARIANT"
echo "OCR ns     : mineru_${NS}_*"

# dirs
mkdir -p "$NEW_PATH"/{raw/"$TOPIC",scripts,.claude/commands,.claude/agents,wiki/notes}
if [ "$VARIANT" = research ]; then WIKIDIRS=(papers concepts gaps experiments); else WIKIDIRS=(lectures topics practice); fi
for d in "${WIKIDIRS[@]}"; do mkdir -p "$NEW_PATH/wiki/$d"; : > "$NEW_PATH/wiki/$d/.gitkeep"; done
: > "$NEW_PATH/raw/$TOPIC/.gitkeep"; : > "$NEW_PATH/wiki/notes/.gitkeep"

# commands / agents (arrays already defined above)
for c in "${CMDS[@]}";   do cp "$SKILL_ROOT/commands/$c.md" "$NEW_PATH/.claude/commands/"; done
for a in "${AGENTS[@]}"; do cp "$SKILL_ROOT/agents/$a.md"   "$NEW_PATH/.claude/agents/";   done
cp "$SKILL_ROOT/scripts/mineru_remote_ocr.py" "$SKILL_ROOT/scripts/mineru_local_ocr.py" "$SKILL_ROOT/scripts/extract_pptx.py" "$NEW_PATH/scripts/"

# bake ns + topic into the OCR scripts (host/user/pass stay in env)
for f in "$NEW_PATH/scripts/mineru_remote_ocr.py" "$NEW_PATH/scripts/mineru_local_ocr.py"; do
  sed -i.bak "s|__WIKI_NS__|${NS}|g; s|__WIKI_TOPIC__|${TOPIC}|g" "$f" && rm -f "$f.bak"
done

# render templates (sed; escape & | \ in values)
esc() { printf '%s' "$1" | sed 's/[&|\\]/\\&/g'; }
P_NAME="$(esc "$NAME")"; P_TOPIC="$(esc "$TOPIC")"; P_NS="$(esc "$NS")"; P_DATE="$(esc "$DATE")"; P_PATH="$(esc "$NEW_PATH")"
render() {
  sed -e "s|{{PROJECT_NAME}}|${P_NAME}|g" -e "s|{{TOPIC}}|${P_TOPIC}|g" \
      -e "s|{{NS}}|${P_NS}|g" -e "s|{{DATE}}|${P_DATE}|g" -e "s|{{NEWPATH}}|${P_PATH}|g" \
      "$1" > "$2"
}
render "$SKILL_ROOT/templates/$VARIANT/CLAUDE.md.tmpl"   "$NEW_PATH/CLAUDE.md"
render "$SKILL_ROOT/templates/$VARIANT/research.md.tmpl" "$NEW_PATH/research.md"
render "$SKILL_ROOT/templates/$VARIANT/README.md.tmpl"   "$NEW_PATH/README.md"

echo
echo "Done -> $NEW_PATH"
echo "Next: cd \"$NEW_PATH\"; start Claude Code; run /wiki-init"
echo "(OCR needs creds via env -- see docs/OCR-SETUP.md; the password stays in local memory, never in the repo)"
