#!/usr/bin/env bash
# Walks three tasks through the routing decisions against the local decision model.
# Uses a throwaway repository and decision log, so your real log is untouched.
set -euo pipefail

MODEL="${1:-strands-decider:2b}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export ORCHESTRA_STATE_DIR="$WORK/state"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
CORE="$SCRIPTS/../core"
CONFIG="$WORK/orchestra.toml"
cat > "$CONFIG" <<EOF
[decider]
backend = "systemone"
rubrics_dir = "$CORE/rubrics"

[backends.systemone]
model = "$MODEL"
EOF

repo="$WORK/repo"
mkdir -p "$repo/src/auth" "$repo/tests"
cd "$repo"
git init -q -b main
printf '# Toy app\nInstall it and recieve updates.\n' > README.md
printf 'def login(username, password, users):\n    return users.get(username) == password\n' > src/auth/session.py
printf 'def get_project(projects, project_id):\n    return projects[project_id]\n' > src/projects.py
printf 'from src.projects import get_project\n\n\ndef test_get_project():\n    assert get_project({1: "a"}, 1) == "a"\n' > tests/test_projects.py
git add . && git -c user.name=demo -c user.email=demo@example.com -c commit.gpgsign=false commit -qm base
BASE="$(git rev-parse HEAD)"

state() { printf '%s\n' "$@" > "$WORK/state.md"; }

decide() {
  local args=("$1" --task "$TASK" --state-file "$WORK/state.md" --config "$CONFIG")
  [[ "${2:-}" == diff ]] && args+=(--git-diff "$BASE")
  orchestra decide "${args[@]}" | python3 "$SCRIPTS/show_decision.py"
}

start() { TASK="$(orchestra task start --title "$1" --config "$CONFIG" | python3 -c 'import json, sys; print(json.load(sys.stdin)["task"])')"; echo; echo "== $1"; }
finish() { orchestra task finish "$TASK" --result accepted --config "$CONFIG" > /dev/null; git checkout -q -- . ; }

echo "Decision model: $MODEL"

start "Fix README typo"
state 'Task: Fix the typo "recieve" in the README.' 'Acceptance criteria: README says "receive".'
decide scope
decide tier
finish

start "Raise ProjectNotFound for unknown ids"
state 'Task: get_project should raise ProjectNotFound instead of KeyError when the id is unknown.' \
      'Acceptance criteria:' '- Unknown id raises ProjectNotFound with the id in the message.' '- Known ids behave as before.'
decide scope
decide tier
printf 'class ProjectNotFound(Exception):\n    pass\n\n\ndef get_project(projects, project_id):\n    try:\n        return projects[project_id]\n    except KeyError:\n        raise ProjectNotFound(f"project {project_id} not found") from None\n' > src/projects.py
decide panel diff
state 'Iteration 1 review findings:' '- generalist: no blocking findings. Suggestion: name the test after the behaviour.' 'Tests: all passing.'
decide verdict
finish

start "Hash passwords with bcrypt"
state 'Task: Store and compare bcrypt password hashes in login instead of plaintext, migrating existing users on their next successful login.' \
      'Acceptance criteria:' '- login verifies bcrypt hashes.' '- Plaintext passwords are replaced by a hash after a successful login.'
decide scope
decide tier
printf 'import bcrypt\n\n\ndef login(username, password, users):\n    stored = users.get(username)\n    if stored is None:\n        return False\n    if not stored.startswith("$2b$"):\n        if stored != password:\n            return False\n        users[username] = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()\n        return True\n    return bcrypt.checkpw(password.encode(), stored.encode())\n' > src/auth/session.py
orchestra signals --git-diff "$BASE" --config "$CONFIG" | sed 's/^/  /'
decide panel diff
state 'Iteration 1 review findings:' '- security: BLOCKING src/auth/session.py:9 plaintext comparison is not constant time. Use hmac.compare_digest.' \
      '- generalist: no blocking findings.' 'Tests: all passing.'
decide verdict
finish

echo
echo "== Decision log"
echo "  $(orchestra log export --config "$CONFIG" | wc -l | tr -d ' ') decisions logged, each with its state, rubric, model choice and final choice"
