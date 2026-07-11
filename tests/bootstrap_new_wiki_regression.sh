#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
BOOTSTRAP="$REPO_ROOT/scripts/bootstrap_new_wiki.sh"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/paper-wiki-bootstrap-test-XXXXXX")"
LAST_CODE=0
LAST_OUTPUT=""

cleanup() {
  case "$TEMP_ROOT" in
    "${TMPDIR:-/tmp}"/paper-wiki-bootstrap-test-*) rm -rf -- "$TEMP_ROOT" ;;
    *) echo "Refusing to remove non-temporary test path: $TEMP_ROOT" >&2 ;;
  esac
}
trap cleanup EXIT

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

file_sha256() {
  local file="$1" digest
  if command -v sha256sum >/dev/null 2>&1; then
    digest="$(sha256sum -- "$file" | awk '{print $1}')"
  elif command -v shasum >/dev/null 2>&1; then
    digest="$(shasum -a 256 -- "$file" | awk '{print $1}')"
  elif command -v openssl >/dev/null 2>&1; then
    digest="$(openssl dgst -sha256 "$file" | awk '{print $NF}')"
  else
    fail 'no SHA-256 tool found (need sha256sum, shasum, or openssl)'
  fi
  [[ "$digest" =~ ^[0-9a-fA-F]{64}$ ]] ||
    fail "invalid SHA-256 output for: $file"
  printf '%s' "$digest"
}

run_bootstrap() {
  local output="$TEMP_ROOT/bootstrap.out"
  set +e
  "$BOOTSTRAP" "$@" >"$output" 2>&1
  LAST_CODE=$?
  set -e
  LAST_OUTPUT="$(cat "$output")"
}

new_current_project() {
  local path="$1" topic="$2" variant="$3"
  run_bootstrap --path "$path" --topic "$topic" --name 'Regression Project' --variant "$variant"
  [ "$LAST_CODE" -eq 0 ] || fail "create failed ($variant): $LAST_OUTPUT"
}

research="$TEMP_ROOT/Research Project With Spaces"
new_current_project "$research" regression-research research
wiki_hash="$(file_sha256 "$research/WIKI.md")"
run_bootstrap --path "$research" --update
[ "$LAST_CODE" -eq 0 ] || fail "research update failed: $LAST_OUTPUT"
[ "$wiki_hash" = "$(file_sha256 "$research/WIKI.md")" ] ||
  fail 'research update did not preserve WIKI.md'

course="$TEMP_ROOT/Course Project"
new_current_project "$course" regression-course course
run_bootstrap --path "$course" --update
[ "$LAST_CODE" -eq 0 ] || fail "course update failed: $LAST_OUTPUT"

missing_wiki="$TEMP_ROOT/Missing Wiki"
new_current_project "$missing_wiki" missing-wiki research
rm -- "$missing_wiki/WIKI.md"
run_bootstrap --path "$missing_wiki" --update
[ "$LAST_CODE" -eq 3 ] || fail "managed project without WIKI.md returned $LAST_CODE"
[ ! -e "$missing_wiki/WIKI.md" ] || fail 'managed project reconstructed WIKI.md from CLAUDE.md'

missing_both="$TEMP_ROOT/Missing Both"
new_current_project "$missing_both" missing-both research
rm -- "$missing_both/WIKI.md" "$missing_both/CLAUDE.md"
run_bootstrap --path "$missing_both" --update
[ "$LAST_CODE" -eq 3 ] || fail "managed project without adapters returned $LAST_CODE"
[ ! -e "$missing_both/CLAUDE.md" ] || fail 'failed update modified a project without WIKI.md'

legacy="$TEMP_ROOT/Legacy Project"
mkdir -p -- "$legacy/.claude/commands" "$legacy/raw/legacy-topic"
printf '%s\n' \
  '# Legacy LLM Wiki' \
  '<!-- paper-wiki-variant: research -->' \
  'wiki/papers/' \
  'CUSTOM LEGACY RULES' >"$legacy/CLAUDE.md"
cp -- "$legacy/CLAUDE.md" "$TEMP_ROOT/legacy.expected"
printf legacy >"$legacy/.claude/commands/wiki-init.md"
printf legacy >"$legacy/.claude/commands/wiki-compile.md"
run_bootstrap --path "$legacy" --update
[ "$LAST_CODE" -eq 0 ] || fail "confirmed legacy migration failed: $LAST_OUTPUT"
cmp -s "$legacy/WIKI.md" "$TEMP_ROOT/legacy.expected" ||
  fail 'legacy CLAUDE.md was not copied exactly to WIKI.md'

thin="$TEMP_ROOT/Thin Adapter"
mkdir -p -- "$thin/.claude/commands" "$thin/raw/thin-topic"
printf '%s\n' \
  '# Claude Code project adapter' \
  '<!-- paper-wiki-variant: research -->' \
  'WIKI.md is the only canonical source for project rules.' \
  'paper-wiki wiki/papers/' >"$thin/CLAUDE.md"
printf legacy-looking >"$thin/.claude/commands/wiki-init.md"
printf legacy-looking >"$thin/.claude/commands/wiki-compile.md"
run_bootstrap --path "$thin" --update
[ "$LAST_CODE" -eq 3 ] || fail "thin adapter migration returned $LAST_CODE"
[ ! -e "$thin/WIKI.md" ] || fail 'thin CLAUDE.md adapter was migrated to WIKI.md'

race="$TEMP_ROOT/Hash Race"
new_current_project "$race" hash-race research
real_python="$(command -v python3)"
wrapper_dir="$TEMP_ROOT/python-wrapper"
mkdir -- "$wrapper_dir"
cat >"$wrapper_dir/python3" <<'WRAPPER'
#!/usr/bin/env bash
set -Eeuo pipefail
if [ "${1:-}" = - ] && [ "${2:-}" = commit ] &&
   [ "${5:-}" = .paper-wiki/project.yaml ] && [ ! -e "$PW_RACE_MARKER" ]; then
  "$PW_REAL_PYTHON" -c '
import os, sys
path = sys.argv[1]
info = os.stat(path)
with open(path, "rb") as stream:
    original = stream.read()
changed = original.replace(
    b"project_name: '\''Regression Project'\''",
    b"project_name: '\''Regression Mutator'\''",
)
if len(changed) != len(original) or changed == original:
    raise SystemExit("race fixture did not make an equal-length change")
with open(path, "r+b", buffering=0) as stream:
    stream.write(changed)
    stream.truncate()
    os.fsync(stream.fileno())
os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
' "$PW_RACE_TARGET"
  : >"$PW_RACE_MARKER"
fi
exec "$PW_REAL_PYTHON" "$@"
WRAPPER
chmod 700 "$wrapper_dir/python3"
export PW_REAL_PYTHON="$real_python"
export PW_RACE_TARGET="$race/.paper-wiki/project.yaml"
export PW_RACE_MARKER="$TEMP_ROOT/race-triggered"
old_path="$PATH"
PATH="$wrapper_dir:$PATH"
run_bootstrap --path "$race" --update
PATH="$old_path"
[ -e "$PW_RACE_MARKER" ] || fail 'deterministic stale-content mutation did not run'
[ "$LAST_CODE" -ne 0 ] || fail 'equal-length stale-content mutation was not rejected'
printf '%s' "$LAST_OUTPUT" | grep -q 'changed during update' ||
  fail "stale-content rejection was not reported: $LAST_OUTPUT"

echo 'Bash bootstrap regression: PASS'
