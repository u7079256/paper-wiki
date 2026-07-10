#!/usr/bin/env bash
# Bootstrap or update a dual-client LLM-Wiki project (macOS/Linux).
# Mirror of bootstrap_new_wiki.ps1. Templates are UTF-8 without a BOM.
set -Eeuo pipefail

NEW_PATH=""
TOPIC=""
NAME=""
VARIANT=""
VARIANT_SET=false
TOPIC_SET=false
SKILL_ROOT=""
UPDATE=false

while [ $# -gt 0 ]; do
  case "$1" in
    --path)       NEW_PATH="$2"; shift 2 ;;
    --topic)      TOPIC="$2"; TOPIC_SET=true; shift 2 ;;
    --name)       NAME="$2"; shift 2 ;;
    --variant)    VARIANT="$2"; VARIANT_SET=true; shift 2 ;;
    --skill-root) SKILL_ROOT="$2"; shift 2 ;;
    --update)     UPDATE=true; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -n "$NEW_PATH" ] || { echo "required: --path" >&2; exit 2; }
if [ "$NEW_PATH" != / ]; then NEW_PATH="${NEW_PATH%/}"; fi
if $VARIANT_SET; then
  case "$VARIANT" in research|course) ;; *) echo "--variant must be research|course" >&2; exit 2 ;; esac
fi

SCRIPT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
[ -n "$SKILL_ROOT" ] || SKILL_ROOT="$(dirname -- "$SCRIPT_DIR")"
SKILL_ROOT="$(cd -P -- "$SKILL_ROOT" && pwd -P)"

fail() {
  echo "$1" >&2
  exit "${2:-1}"
}

require_file() {
  [ -f "$1" ] || fail "Required paper-wiki file is missing: $1"
}

validate_topic() {
  [[ "$1" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] ||
    fail "Topic must be lower-case kebab-case (for example: my-topic)." 2
}

manifest_scalar() {
  local file="$1" key="$2" value
  [ -f "$file" ] || return 0
  value="$(sed -n "s/^[[:space:]]*${key}[[:space:]]*:[[:space:]]*//p" "$file" | head -n 1)"
  if [[ "$value" == \'*\' ]] && [ "${#value}" -ge 2 ]; then
    value="${value:1:${#value}-2}"
    value="${value//\'\'/\'}"
  elif [[ "$value" == \"*\" ]] && [ "${#value}" -ge 2 ]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "$value"
}

infer_variant_file() {
  local file="$1" marker
  [ -f "$file" ] || return 0
  marker="$(grep -Eio 'paper-wiki-variant[[:space:]]*:[[:space:]]*(research|course)' "$file" | head -n 1 || true)"
  if [ -n "$marker" ]; then
    printf '%s' "$marker" | grep -Eio '(research|course)$' | tr '[:upper:]' '[:lower:]'
  elif grep -Eqi 'wiki/papers/' "$file"; then
    printf research
  elif grep -Eqi 'wiki/lectures/' "$file"; then
    printf course
  else
    marker="$(grep -Eio 'paper-wiki.{0,100}(research|course)' "$file" | head -n 1 || true)"
    [ -z "$marker" ] || printf '%s' "$marker" | grep -Eio '(research|course)$' | tr '[:upper:]' '[:lower:]'
  fi
}

yaml_quote() {
  local value="$1"
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || fail "Manifest values must not contain newlines." 2
  value="${value//\'/\'\'}"
  printf "'%s'" "$value"
}

write_manifest() {
  local path="$1"
  mkdir -p -- "$(dirname -- "$path")"
  {
    echo "spec: 'llm-wiki/1.1'"
    printf 'variant: %s\n' "$(yaml_quote "$VARIANT")"
    printf 'topic: %s\n' "$(yaml_quote "$TOPIC")"
    printf 'project_name: %s\n' "$(yaml_quote "$NAME")"
    printf 'scaffold_version: %s\n' "$(yaml_quote "$SCAFFOLD_VERSION")"
    echo 'clients:'
    echo '  - claude-code'
    echo '  - codex'
    echo "canonical_rules: 'WIKI.md'"
  } > "$path"
}

set_variant_assets() {
  if [ "$VARIANT" = research ]; then
    CMDS=(wiki-init wiki-teach wiki-compile wiki-search-latest wiki-critique wiki-ideate)
    AGENTS=(wiki-searcher wiki-critic wiki-ideator)
  else
    CMDS=(wiki-init wiki-teach wiki-compile wiki-critique)
    AGENTS=(wiki-critic)
  fi
}

ALL_CMDS=(wiki-init wiki-teach wiki-compile wiki-search-latest wiki-critique wiki-ideate wiki-ask)
ALL_AGENTS=(wiki-searcher wiki-critic wiki-ideator)
PROTOCOL_DOCS=(llm-wiki.protocol.yaml OCR-SETUP.md GOTCHAS.md)
MANAGED_TREES=(.claude/commands .claude/agents .agents/skills/paper-wiki-project .paper-wiki)

managed_file_relatives() {
  local include_wiki="$1" name
  printf '%s\n' CLAUDE.md AGENTS.md .agents/skills/paper-wiki-project/SKILL.md
  for name in "${ALL_CMDS[@]}"; do printf '.claude/commands/%s.md\n' "$name"; done
  for name in "${ALL_AGENTS[@]}"; do printf '.claude/agents/%s.md\n' "$name"; done
  for name in "${PROTOCOL_DOCS[@]}"; do printf '.paper-wiki/docs/%s\n' "$name"; done
  $include_wiki && printf '%s\n' WIKI.md
  printf '%s\n' .paper-wiki/project.yaml
}

assert_within_root() {
  local path="$1" physical
  case "$path" in
    "$PROJECT_ROOT_REAL"|"$PROJECT_ROOT_REAL"/*) ;;
    *) fail "Managed target escapes project root: $path" ;;
  esac
  if [ -e "$path" ] && [ ! -L "$path" ]; then
    if [ -d "$path" ]; then
      physical="$(cd -P -- "$path" && pwd -P)"
    else
      physical="$(cd -P -- "$(dirname -- "$path")" && pwd -P)/$(basename -- "$path")"
    fi
    case "$physical" in
      "$PROJECT_ROOT_REAL"|"$PROJECT_ROOT_REAL"/*) ;;
      *) fail "Managed target resolves outside project root: $path" ;;
    esac
  fi
}

assert_path_chain_safe() {
  local relative="$1" current="$NEW_PATH" rest component
  assert_within_root "$NEW_PATH/$relative"
  [ ! -L "$NEW_PATH" ] || fail "Refusing symlink project root: $NEW_PATH"
  rest="$relative"
  while [ -n "$rest" ]; do
    case "$rest" in
      */*) component="${rest%%/*}"; rest="${rest#*/}" ;;
      *) component="$rest"; rest="" ;;
    esac
    [ -n "$component" ] || continue
    current="$current/$component"
    [ ! -L "$current" ] || fail "Refusing symlink in managed project path: $current"
    assert_within_root "$current"
  done
}

assert_managed_security() {
  local include_wiki="$1" relative tree found
  [ ! -L "$NEW_PATH" ] || fail "Refusing symlink project root: $NEW_PATH"
  while IFS= read -r relative; do
    assert_path_chain_safe "$relative"
    if [ -e "$NEW_PATH/$relative" ] && [ -d "$NEW_PATH/$relative" ]; then
      fail "Managed file target is a directory: $NEW_PATH/$relative"
    fi
  done < <(managed_file_relatives "$include_wiki")
  for tree in "${MANAGED_TREES[@]}"; do
    assert_path_chain_safe "$tree"
    if [ -e "$NEW_PATH/$tree" ] && [ ! -d "$NEW_PATH/$tree" ]; then
      fail "Managed directory path is not a directory: $NEW_PATH/$tree"
    fi
    if [ -d "$NEW_PATH/$tree" ]; then
      if ! found="$(find "$NEW_PATH/$tree" -type l -print -quit)"; then
        fail "Cannot inspect managed project tree: $NEW_PATH/$tree"
      fi
      [ -z "$found" ] || fail "Refusing symlink in managed project tree: $found"
    fi
  done
}

secure_fs() {
  python3 - "$@" <<'PY'
import hashlib
import os
import secrets
import stat
import sys


def die(message):
    raise SystemExit(message)


def split_relative(value):
    if not value or os.path.isabs(value):
        die(f"Unsafe managed relative path: {value}")
    parts = value.split("/")
    if any(part in ("", ".", "..") or "\\" in part for part in parts):
        die(f"Unsafe managed relative path: {value}")
    return parts


def open_root(path, expected=None):
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory or not os.supports_dir_fd:
        die("Secure update requires Python dir_fd, O_DIRECTORY, and O_NOFOLLOW support.")
    fd = os.open(path, os.O_RDONLY | directory | nofollow)
    info = os.fstat(fd)
    identity = f"{info.st_dev}:{info.st_ino}"
    if expected is not None and identity != expected:
        os.close(fd)
        die(f"Project root changed during update: expected {expected}, got {identity}")
    return fd, identity


def open_parent(root_fd, relative):
    parts = split_relative(relative)
    fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=fd,
            )
            info = os.fstat(next_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(next_fd)
                die(f"Managed ancestor is not a directory: {component}")
            os.close(fd)
            fd = next_fd
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def entry_stat(parent_fd, name):
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def require_regular(info, label):
    if info is None or not stat.S_ISREG(info.st_mode):
        die(f"Managed target is not a regular file: {label}")
    if info.st_nlink != 1:
        die(f"Refusing hard-linked managed file (link count {info.st_nlink}): {label}")


def state_token(info):
    if info is None:
        return "0"
    require_regular(info, "managed target")
    return f"1:{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"


def require_state(info, expected, label):
    actual = state_token(info)
    if actual != expected:
        die(f"Managed target changed during update: {label} (expected {expected}, got {actual})")


def copy_fd(source_fd, destination_fd):
    while True:
        block = os.read(source_fd, 1024 * 1024)
        if not block:
            break
        view = memoryview(block)
        while view:
            written = os.write(destination_fd, view)
            view = view[written:]


def make_parent_temp(parent_fd, source):
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    temporary = ".paper-wiki-write-" + secrets.token_hex(16)
    try:
        mode = stat.S_IMODE(os.fstat(source_fd).st_mode)
        destination_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            mode or 0o600,
            dir_fd=parent_fd,
        )
        try:
            copy_fd(source_fd, destination_fd)
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)
    return temporary


def hash_fd(fd):
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            return digest.digest()
        digest.update(block)


def verify_same(parent_fd, name, source):
    target_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        require_regular(os.fstat(target_fd), name)
        if hash_fd(target_fd) != hash_fd(source_fd):
            die(f"File verification failed: {name}")
    finally:
        os.close(target_fd)
        os.close(source_fd)


command = sys.argv[1]
root = sys.argv[2]
if command == "root-id":
    root_fd, identity = open_root(root)
    os.close(root_fd)
    print(identity)
    raise SystemExit(0)

expected_root = sys.argv[3]
root_fd, _ = open_root(root, expected_root)
try:
    if command == "inspect":
        relative = sys.argv[4]
        parent_fd, name = open_parent(root_fd, relative)
        try:
            print(state_token(entry_stat(parent_fd, name)))
        finally:
            os.close(parent_fd)
    elif command == "mkdir":
        relative = sys.argv[4]
        parent_fd, name = open_parent(root_fd, relative)
        try:
            info = entry_stat(parent_fd, name)
            if info is None:
                os.mkdir(name, 0o755, dir_fd=parent_fd)
                info = entry_stat(parent_fd, name)
                if info is None or not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    die(f"Created managed directory cannot be verified: {relative}")
                print("1")
            else:
                if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    die(f"Managed directory path is unsafe: {relative}")
                print("0")
        finally:
            os.close(parent_fd)
    elif command == "commit":
        relative, source, backup, expected = sys.argv[4:8]
        parent_fd, name = open_parent(root_fd, relative)
        temporary = None
        try:
            before = entry_stat(parent_fd, name)
            require_state(before, expected, relative)
            existed = before is not None
            if existed:
                target_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    current = os.fstat(target_fd)
                    require_state(current, expected, relative)
                    os.makedirs(os.path.dirname(backup), exist_ok=True)
                    backup_fd = os.open(
                        backup,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        stat.S_IMODE(current.st_mode) or 0o600,
                    )
                    try:
                        copy_fd(target_fd, backup_fd)
                        os.fsync(backup_fd)
                    finally:
                        os.close(backup_fd)
                finally:
                    os.close(target_fd)
            require_state(entry_stat(parent_fd, name), expected, relative)
            if source != "-":
                temporary = make_parent_temp(parent_fd, source)
                require_state(entry_stat(parent_fd, name), expected, relative)
                os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                temporary = None
            elif existed:
                os.unlink(name, dir_fd=parent_fd)
                if entry_stat(parent_fd, name) is not None:
                    die(f"Deletion verification failed: {relative}")
            try:
                os.fsync(parent_fd)
            except OSError:
                pass
            print("1" if existed else "0")
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.close(parent_fd)
    elif command == "restore":
        relative, backup, existed = sys.argv[4:7]
        parent_fd, name = open_parent(root_fd, relative)
        temporary = None
        try:
            current = entry_stat(parent_fd, name)
            if current is not None:
                require_regular(current, relative)
            if existed == "1":
                temporary = make_parent_temp(parent_fd, backup)
                os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                temporary = None
                verify_same(parent_fd, name, backup)
            else:
                if current is not None:
                    os.unlink(name, dir_fd=parent_fd)
                if entry_stat(parent_fd, name) is not None:
                    die(f"New managed file still exists after rollback: {relative}")
            try:
                os.fsync(parent_fd)
            except OSError:
                pass
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.close(parent_fd)
    elif command == "rmdir":
        relative = sys.argv[4]
        parent_fd, name = open_parent(root_fd, relative)
        try:
            info = entry_stat(parent_fd, name)
            if info is None:
                raise SystemExit(0)
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                die(f"Created directory changed type during rollback: {relative}")
            os.rmdir(name, dir_fd=parent_fd)
            if entry_stat(parent_fd, name) is not None:
                die(f"Created directory still exists after rollback: {relative}")
        finally:
            os.close(parent_fd)
    else:
        die(f"Unknown secure filesystem operation: {command}")
finally:
    os.close(root_fd)
PY
}

preflight_sources() {
  local for_update="$1" name
  require_file "$SKILL_ROOT/VERSION"
  for name in "${CMDS[@]}"; do require_file "$SKILL_ROOT/commands/$name.md"; done
  for name in "${AGENTS[@]}"; do require_file "$SKILL_ROOT/agents/$name.md"; done
  for name in "${PROTOCOL_DOCS[@]}"; do require_file "$SKILL_ROOT/docs/$name"; done
  require_file "$SKILL_ROOT/templates/$VARIANT/CLAUDE.md.tmpl"
  require_file "$SKILL_ROOT/templates/common/AGENTS.md.tmpl"
  require_file "$SKILL_ROOT/templates/common/paper-wiki-project.SKILL.md.tmpl"
  if ! $for_update; then
    require_file "$SKILL_ROOT/templates/$VARIANT/WIKI.md.tmpl"
    require_file "$SKILL_ROOT/templates/$VARIANT/research.md.tmpl"
    require_file "$SKILL_ROOT/templates/$VARIANT/README.md.tmpl"
    for name in mineru_remote_ocr.py mineru_local_ocr.py extract_pptx.py; do
      require_file "$SKILL_ROOT/scripts/$name"
    done
  fi
}

copy_file() {
  local source="$1" destination="$2"
  mkdir -p -- "$(dirname -- "$destination")"
  cp -- "$source" "$destination"
}

copy_claude_assets() {
  local root="$1" name
  for name in "${CMDS[@]}"; do
    copy_file "$SKILL_ROOT/commands/$name.md" "$root/.claude/commands/$name.md"
  done
  for name in "${AGENTS[@]}"; do
    copy_file "$SKILL_ROOT/agents/$name.md" "$root/.claude/agents/$name.md"
  done
}

copy_protocol_docs() {
  local root="$1" name
  for name in "${PROTOCOL_DOCS[@]}"; do
    copy_file "$SKILL_ROOT/docs/$name" "$root/.paper-wiki/docs/$name"
  done
}

esc() { printf '%s' "$1" | sed 's/[&|\\]/\\&/g'; }

render() {
  local template="$1" output="$2"
  local p_name p_topic p_ns p_date p_path p_version
  require_file "$template"
  mkdir -p -- "$(dirname -- "$output")"
  p_name="$(esc "$NAME")"; p_topic="$(esc "$TOPIC")"; p_ns="$(esc "$NS")"
  p_date="$(esc "$DATE")"; p_path="$(esc "$NEW_PATH")"; p_version="$(esc "$SCAFFOLD_VERSION")"
  sed -e "s|{{PROJECT_NAME}}|${p_name}|g" -e "s|{{TOPIC}}|${p_topic}|g" \
      -e "s|{{NS}}|${p_ns}|g" -e "s|{{DATE}}|${p_date}|g" \
      -e "s|{{NEWPATH}}|${p_path}|g" -e "s|{{SCAFFOLD_VERSION}}|${p_version}|g" \
      "$template" > "$output"
}

write_adapters() {
  local root="$1"
  render "$SKILL_ROOT/templates/$VARIANT/CLAUDE.md.tmpl" "$root/CLAUDE.md"
  render "$SKILL_ROOT/templates/common/AGENTS.md.tmpl" "$root/AGENTS.md"
  render "$SKILL_ROOT/templates/common/paper-wiki-project.SKILL.md.tmpl" \
    "$root/.agents/skills/paper-wiki-project/SKILL.md"
}

build_managed_stage() {
  local root="$1"
  mkdir -p -- "$root"
  copy_claude_assets "$root"
  copy_protocol_docs "$root"
  write_adapters "$root"
  write_manifest "$root/.paper-wiki/project.yaml"
}

build_create_stage() {
  local root="$1" name escaped_ns escaped_topic
  mkdir -p -- "$root/raw/$TOPIC" "$root/scripts" \
    "$root/.claude/commands" "$root/.claude/agents" \
    "$root/.agents/skills/paper-wiki-project" "$root/.paper-wiki/docs" \
    "$root/wiki/notes"
  if [ "$VARIANT" = research ]; then
    WIKI_DIRS=(papers concepts gaps experiments)
  else
    WIKI_DIRS=(lectures topics practice)
  fi
  for name in "${WIKI_DIRS[@]}"; do mkdir -p -- "$root/wiki/$name"; : > "$root/wiki/$name/.gitkeep"; done
  : > "$root/raw/$TOPIC/.gitkeep"
  : > "$root/wiki/notes/.gitkeep"
  copy_claude_assets "$root"
  copy_protocol_docs "$root"
  for name in mineru_remote_ocr.py mineru_local_ocr.py extract_pptx.py; do
    copy_file "$SKILL_ROOT/scripts/$name" "$root/scripts/$name"
  done
  escaped_ns="$(esc "$NS")"
  escaped_topic="$(esc "$TOPIC")"
  for name in mineru_remote_ocr.py mineru_local_ocr.py; do
    sed -e "s|__WIKI_NS__|${escaped_ns}|g" -e "s|__WIKI_TOPIC__|${escaped_topic}|g" \
      "$root/scripts/$name" > "$root/scripts/$name.new"
    mv -- "$root/scripts/$name.new" "$root/scripts/$name"
  done
  render "$SKILL_ROOT/templates/$VARIANT/WIKI.md.tmpl" "$root/WIKI.md"
  render "$SKILL_ROOT/templates/$VARIANT/research.md.tmpl" "$root/research.md"
  render "$SKILL_ROOT/templates/$VARIANT/README.md.tmpl" "$root/README.md"
  write_adapters "$root"
  write_manifest "$root/.paper-wiki/project.yaml"
}

TX_ROOT=""
CREATE_STAGE=""
COMMITTING=false
CREATE_RESTORE_EMPTY=false
TX_TARGETS=()
TX_EXISTED=()
TX_EXPECTED=()
COMMITTED_TARGETS=()
COMMITTED_EXISTED=()
CREATED_DIRS=()
PROJECT_ROOT_ID=""

remove_temp_tree() {
  local path="$1"
  [ -n "$path" ] || return 0
  case "$(basename -- "$path")" in
    .paper-wiki-update-*|.paper-wiki-bootstrap-*) ;;
    *) echo "Refusing unsafe temporary directory removal: $path" >&2; return 1 ;;
  esac
  [ ! -e "$path" ] || rm -rf -- "$path"
}

rollback_update() {
  local i relative failed=0
  set +e
  for ((i=${#COMMITTED_TARGETS[@]}-1; i>=0; i--)); do
    relative="${COMMITTED_TARGETS[$i]}"
    if ! secure_fs restore "$NEW_PATH" "$PROJECT_ROOT_ID" "$relative" \
      "$TX_ROOT/backup/$relative" "${COMMITTED_EXISTED[$i]}"; then
      echo "Rollback verification failed for: $relative" >&2
      failed=1
    fi
  done
  for ((i=${#CREATED_DIRS[@]}-1; i>=0; i--)); do
    relative="${CREATED_DIRS[$i]}"
    if ! secure_fs rmdir "$NEW_PATH" "$PROJECT_ROOT_ID" "$relative"; then
      echo "Rollback verification failed for created directory: $relative" >&2
      failed=1
    fi
  done
  set -e
  [ "$failed" -eq 0 ]
}

cleanup() {
  local status=$?
  trap - ERR
  set +e
  if $COMMITTING; then
    if rollback_update; then
      echo "paper-wiki update failed and rollback was verified." >&2
    else
      echo "paper-wiki update failed; rollback incomplete." >&2
      echo "Backup preserved at: $TX_ROOT/backup" >&2
      exit 4
    fi
  fi
  if $CREATE_RESTORE_EMPTY && [ ! -d "$NEW_PATH" ]; then mkdir -p -- "$NEW_PATH"; fi
  [ -z "$TX_ROOT" ] || remove_temp_tree "$TX_ROOT"
  [ -z "$CREATE_STAGE" ] || remove_temp_tree "$CREATE_STAGE"
  exit "$status"
}
trap cleanup EXIT

DATE="$(date +%Y-%m-%d)"

if $UPDATE; then
  [ ! -L "$NEW_PATH" ] || fail "Refusing symlink project root: $NEW_PATH"
  [ -d "$NEW_PATH" ] || fail "Project path does not exist: $NEW_PATH"
  NEW_PATH="$(cd -P -- "$NEW_PATH" && pwd -P)"
  PROJECT_ROOT_REAL="$NEW_PATH"
  assert_managed_security true

  MANIFEST="$NEW_PATH/.paper-wiki/project.yaml"
  MANIFEST_SPEC="$(manifest_scalar "$MANIFEST" spec)"
  MANIFEST_VARIANT="$(manifest_scalar "$MANIFEST" variant)"
  MANIFEST_TOPIC="$(manifest_scalar "$MANIFEST" topic)"
  MANIFEST_NAME="$(manifest_scalar "$MANIFEST" project_name)"
  WIKI_VARIANT="$(infer_variant_file "$NEW_PATH/WIKI.md")"
  CLAUDE_VARIANT="$(infer_variant_file "$NEW_PATH/CLAUDE.md")"
  MANAGED=false
  case "$MANIFEST_SPEC" in llm-wiki/*) MANAGED=true ;; esac
  if [ -d "$NEW_PATH/.paper-wiki" ] && [ -f "$NEW_PATH/WIKI.md" ] &&
     grep -Eq 'paper-wiki-variant[[:space:]]*:[[:space:]]*(research|course)' "$NEW_PATH/WIKI.md"; then
    MANAGED=true
  fi
  if [ -f "$NEW_PATH/.claude/commands/wiki-init.md" ] &&
     [ -f "$NEW_PATH/.claude/commands/wiki-compile.md" ] &&
     [ -n "$CLAUDE_VARIANT" ] && grep -Eiq 'LLM Wiki|paper-wiki' "$NEW_PATH/CLAUDE.md"; then
    MANAGED=true
  fi
  $MANAGED || fail "This does not look like a paper-wiki project. No paper-wiki manifest, marked WIKI.md, or legacy command/template signature was found."

  EVIDENCE_VARIANT=""
  EVIDENCE_SOURCE=""
  add_variant_evidence() {
    local source="$1" value="$2"
    case "$value" in research|course) ;; *) return 0 ;; esac
    if [ -n "$EVIDENCE_VARIANT" ] && [ "$EVIDENCE_VARIANT" != "$value" ]; then
      fail "Variant conflict across project metadata: $EVIDENCE_SOURCE=$EVIDENCE_VARIANT, $source=$value." 3
    fi
    EVIDENCE_VARIANT="$value"
    EVIDENCE_SOURCE="$source"
  }
  add_variant_evidence project.yaml "$MANIFEST_VARIANT"
  add_variant_evidence WIKI.md "$WIKI_VARIANT"
  add_variant_evidence CLAUDE.md "$CLAUDE_VARIANT"

  INFERRED=""
  INFERRED_SOURCE=""
  case "$MANIFEST_VARIANT" in
    research|course) INFERRED="$MANIFEST_VARIANT"; INFERRED_SOURCE=project.yaml ;;
    *) if [ -n "$WIKI_VARIANT" ]; then INFERRED="$WIKI_VARIANT"; INFERRED_SOURCE=WIKI.md
       elif [ -n "$CLAUDE_VARIANT" ]; then INFERRED="$CLAUDE_VARIANT"; INFERRED_SOURCE=CLAUDE.md; fi ;;
  esac
  if $VARIANT_SET && [ -n "$INFERRED" ] && [ "$VARIANT" != "$INFERRED" ]; then
    fail "Variant conflict: requested '$VARIANT' but $INFERRED_SOURCE says '$INFERRED'." 3
  fi
  if ! $VARIANT_SET; then
    [ -n "$INFERRED" ] || fail "Cannot infer variant. Pass --variant research or --variant course." 2
    VARIANT="$INFERRED"
  fi

  RAW_TOPIC=""
  RAW_TOPICS=()
  if [ -d "$NEW_PATH/raw" ]; then
    for candidate in "$NEW_PATH"/raw/*; do
      [ -d "$candidate" ] && [ ! -L "$candidate" ] || continue
      RAW_TOPICS+=("$(basename -- "$candidate")")
    done
    if [ "${#RAW_TOPICS[@]}" -gt 1 ]; then
      fail "Topic conflict: multiple raw topic directories were found: ${RAW_TOPICS[*]}." 3
    fi
    if [ "${#RAW_TOPICS[@]}" -eq 1 ]; then RAW_TOPIC="${RAW_TOPICS[0]}"; fi
  fi
  if $TOPIC_SET; then
    validate_topic "$TOPIC"
    if [ -n "$MANIFEST_TOPIC" ] && [ "$TOPIC" != "$MANIFEST_TOPIC" ]; then
      fail "Topic conflict: requested '$TOPIC' but project.yaml says '$MANIFEST_TOPIC'." 3
    fi
    if [ -n "$RAW_TOPIC" ] && [ "$TOPIC" != "$RAW_TOPIC" ]; then
      fail "Topic conflict: requested '$TOPIC' but raw directory says '$RAW_TOPIC'." 3
    fi
  else
    if [ -n "$MANIFEST_TOPIC" ] && [ -n "$RAW_TOPIC" ] && [ "$MANIFEST_TOPIC" != "$RAW_TOPIC" ]; then
      fail "Topic conflict across project metadata: project.yaml=$MANIFEST_TOPIC, raw=$RAW_TOPIC." 3
    fi
    if [ -n "$MANIFEST_TOPIC" ]; then TOPIC="$MANIFEST_TOPIC"
    elif [ -n "$RAW_TOPIC" ]; then TOPIC="$RAW_TOPIC"
    else TOPIC="$(basename -- "$NEW_PATH")"; fi
  fi
  validate_topic "$TOPIC"
  [ -n "$NAME" ] || NAME="$MANIFEST_NAME"
  [ -n "$NAME" ] || NAME="$(basename -- "$NEW_PATH")"
  NS="$(printf '%s' "$TOPIC" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]\+/_/g; s/^_//; s/_$//')"
  set_variant_assets

  require_file "$SKILL_ROOT/VERSION"
  SCAFFOLD_VERSION="$(tr -d '\r\n' < "$SKILL_ROOT/VERSION")"
  [ -n "$SCAFFOLD_VERSION" ] || fail "VERSION is empty."
  preflight_sources true

  MIGRATE_WIKI=false
  if [ ! -f "$NEW_PATH/WIKI.md" ] && [ -f "$NEW_PATH/CLAUDE.md" ]; then MIGRATE_WIKI=true; fi
  command -v python3 >/dev/null 2>&1 ||
    fail "Secure update requires Python 3 with dir_fd and O_NOFOLLOW support." 2
  PROJECT_ROOT_ID="$(secure_fs root-id "$NEW_PATH")" ||
    fail "Cannot establish a secure project-root handle for update." 2
  PROJECT_PARENT="$(dirname -- "$NEW_PATH")"
  TX_ROOT="$(mktemp -d "$PROJECT_PARENT/.paper-wiki-update-XXXXXX")"
  mkdir -p -- "$TX_ROOT/stage" "$TX_ROOT/backup"
  build_managed_stage "$TX_ROOT/stage"
  if $MIGRATE_WIKI; then copy_file "$NEW_PATH/CLAUDE.md" "$TX_ROOT/stage/WIKI.md"; fi

  while IFS= read -r relative; do TX_TARGETS+=("$relative"); done < <(managed_file_relatives "$MIGRATE_WIKI")
  assert_managed_security "$MIGRATE_WIKI"
  for relative in "${TX_TARGETS[@]}"; do
    if ! expected="$(secure_fs inspect "$NEW_PATH" "$PROJECT_ROOT_ID" "$relative")"; then
      fail "Secure managed-target preflight failed: $relative"
    fi
    TX_EXPECTED+=("$expected")
    case "$expected" in
      0) TX_EXISTED+=(0) ;;
      1:*) TX_EXISTED+=(1) ;;
      *) fail "Invalid secure preflight state for: $relative" ;;
    esac
  done

  COMMITTING=true
  for relative in .paper-wiki .paper-wiki/docs .claude .claude/commands .claude/agents .agents .agents/skills .agents/skills/paper-wiki-project; do
    if ! created="$(secure_fs mkdir "$NEW_PATH" "$PROJECT_ROOT_ID" "$relative")"; then
      fail "Secure managed-directory creation failed: $relative"
    fi
    [ "$created" != 1 ] || CREATED_DIRS+=("$relative")
  done
  for ((i=0; i<${#TX_TARGETS[@]}; i++)); do
    relative="${TX_TARGETS[$i]}"
    source="-"
    [ ! -f "$TX_ROOT/stage/$relative" ] || source="$TX_ROOT/stage/$relative"
    if [ "$source" = - ] && [ "${TX_EXISTED[$i]}" = 0 ]; then continue; fi
    if ! committed_existed="$(secure_fs commit "$NEW_PATH" "$PROJECT_ROOT_ID" \
      "$relative" "$source" "$TX_ROOT/backup/$relative" "${TX_EXPECTED[$i]}")"; then
      fail "Secure atomic commit failed: $relative"
    fi
    [ "$committed_existed" = "${TX_EXISTED[$i]}" ] ||
      fail "Secure commit state mismatch: $relative"
    COMMITTED_TARGETS+=("$relative")
    COMMITTED_EXISTED+=("${TX_EXISTED[$i]}")
  done
  COMMITTING=false
  remove_temp_tree "$TX_ROOT"
  TX_ROOT=""
  if $MIGRATE_WIKI; then echo "Migrated legacy CLAUDE.md to canonical WIKI.md."; fi
  echo "Updated paper-wiki scaffold $SCAFFOLD_VERSION ($VARIANT)."
  echo "Preserved WIKI.md, research.md and README.md."
  exit 0
fi

$TOPIC_SET && [ -n "$TOPIC" ] || fail "required: --path and --topic" 2
validate_topic "$TOPIC"
[ -n "$NAME" ] || NAME="$TOPIC"
if ! $VARIANT_SET; then VARIANT=research; fi
NS="$(printf '%s' "$TOPIC" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]\+/_/g; s/^_//; s/_$//')"
set_variant_assets

[ ! -L "$NEW_PATH" ] || fail "Refusing symlink project root: $NEW_PATH"
if [ -e "$NEW_PATH" ] && [ ! -d "$NEW_PATH" ]; then
  fail "Project path already exists and is not a directory: $NEW_PATH"
fi
if [ -d "$NEW_PATH" ] && [ -n "$(find "$NEW_PATH" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  fail "Refusing to create in an existing non-empty directory: $NEW_PATH"
fi

require_file "$SKILL_ROOT/VERSION"
SCAFFOLD_VERSION="$(tr -d '\r\n' < "$SKILL_ROOT/VERSION")"
[ -n "$SCAFFOLD_VERSION" ] || fail "VERSION is empty."
preflight_sources false

PARENT="$(dirname -- "$NEW_PATH")"
BASE="$(basename -- "$NEW_PATH")"
mkdir -p -- "$PARENT"
PARENT="$(cd -P -- "$PARENT" && pwd -P)"
NEW_PATH="$PARENT/$BASE"
PROJECT_ROOT_REAL="$NEW_PATH"
CREATE_STAGE="$PARENT/.paper-wiki-bootstrap-$$-${RANDOM:-0}"
[ ! -e "$CREATE_STAGE" ] || fail "Temporary path already exists: $CREATE_STAGE"
mkdir -- "$CREATE_STAGE"
build_create_stage "$CREATE_STAGE"

[ ! -L "$NEW_PATH" ] || fail "Refusing symlink project root: $NEW_PATH"
if [ -e "$NEW_PATH" ]; then
  [ -d "$NEW_PATH" ] || fail "Project path already exists and is not a directory: $NEW_PATH"
  [ -z "$(find "$NEW_PATH" -mindepth 1 -maxdepth 1 -print -quit)" ] ||
    fail "Refusing to create in an existing non-empty directory: $NEW_PATH"
  rmdir -- "$NEW_PATH"
  CREATE_RESTORE_EMPTY=true
fi
mv -- "$CREATE_STAGE" "$NEW_PATH"
CREATE_STAGE=""
CREATE_RESTORE_EMPTY=false

echo "Skill root : $SKILL_ROOT"
echo "New project: $NEW_PATH"
echo "Variant    : $VARIANT"
echo "OCR ns     : mineru_${NS}_*"
echo "Done -> $NEW_PATH"
echo "Next: open this project in Claude Code or Codex and ask to initialize the wiki."
