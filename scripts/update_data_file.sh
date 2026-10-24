#!/usr/bin/env bash
# update_data_file.sh — clone the dependency-graph repo, copy the JSON into data/<owner>_<repo>.json,
# rebuild graph/inverted.json in the same commit, then push, retrying on conflict.
#
# The graph is rebuilt here, not by build-graph.yml: this commit carries [skip ci] (so that
# every repository's update does not start a workflow), and a graph left to build-graph.yml
# therefore stayed frozen (inverted.json of 2026-06-08 while data/ moved every day), so that
# ci/build-impacted rebuilt too few consumers (Vidocq/vidocq#285).
#
# Usage: update_data_file.sh --repo owner/repo --data-json /tmp/deps.json --token <pat>
set -euo pipefail

REPO=""
DATA_JSON=""
TOKEN=""
GRAPH_REPO_URL="${GRAPH_REPO_URL:-https://codefloe.com/Vidocq/dep-graph.git}"
GRAPH_BRANCH="${GRAPH_BRANCH:-main}"
MAX_RETRIES=5

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)       REPO="$2"; shift 2 ;;
    --data-json)  DATA_JSON="$2"; shift 2 ;;
    --token)      TOKEN="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$REPO" || -z "$DATA_JSON" || -z "$TOKEN" ]]; then
  echo "Usage: $0 --repo owner/repo --data-json /tmp/deps.json --token <pat>" >&2
  exit 2
fi

if [[ ! -f "$DATA_JSON" ]]; then
  echo "update_data_file.sh: file not found: $DATA_JSON" >&2
  exit 1
fi

# Check first that the JSON parses and that its "repo" field matches.
python3 -c "
import json, sys
d = json.load(open('$DATA_JSON'))
assert d.get('repo') == '$REPO', f'repo mismatch: {d.get(\"repo\")} vs $REPO'
assert isinstance(d.get('produces'), list)
assert isinstance(d.get('consumes'), list)
" || { echo "update_data_file.sh: invalid JSON" >&2; exit 1; }

# The script works inside a clone: resolve the data file before leaving the caller's directory.
DATA_JSON="$(cd "$(dirname "$DATA_JSON")" && pwd)/$(basename "$DATA_JSON")"

# slug: owner/repo -> owner_repo
SLUG="${REPO/\//_}"
DATA_FILE="data/${SLUG}.json"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# URL with basic auth (the token as password, "vidocq-bot" as a placeholder user)
AUTH_URL="${GRAPH_REPO_URL/https:\/\//https://vidocq-bot:${TOKEN}@}"

git clone --depth 1 --branch "$GRAPH_BRANCH" "$AUTH_URL" "$WORK/repo" >/dev/null 2>&1
cd "$WORK/repo"
git config user.name "vidocq-bot"
git config user.email "bot@vidocq.dev"

# Writes the data file and rebuilds the graph on top of the current branch head, then commits.
# Returns 10 when the data file is already up to date, 1 on any failure.
update_and_commit() {
  mkdir -p data
  cp "$DATA_JSON" "$DATA_FILE" || return 1
  if git diff --quiet -- "$DATA_FILE" && ! git status --porcelain | grep -q "$DATA_FILE"; then
    return 10
  fi
  python3 scripts/build_inverted_graph.py >/dev/null || return 1
  git add "$DATA_FILE" graph/inverted.json || return 1
  git commit -m "chore(graph): update ${REPO} [skip ci]" >/dev/null || return 1
}

# Runs update_and_commit and exits the script when there is nothing to push or it failed.
commit_or_exit() {
  local rc=0
  update_and_commit || rc=$?
  case "$rc" in
    0) ;;
    10) echo "update_data_file.sh: no change, nothing to commit."; exit 0 ;;
    *) echo "update_data_file.sh: could not write the data file or rebuild the graph." >&2; exit 1 ;;
  esac
}

commit_or_exit

attempt=1
while (( attempt <= MAX_RETRIES )); do
  if git push origin "$GRAPH_BRANCH" 2>/tmp/push.err; then
    echo "update_data_file.sh: push OK ($DATA_FILE, graph/inverted.json) attempt $attempt"
    exit 0
  fi
  echo "update_data_file.sh: push attempt $attempt failed, starting again from the new branch head..." >&2
  cat /tmp/push.err >&2
  # Another repository pushed meanwhile: inverted.json depends on every data file, so it is rebuilt
  # on top of the new head rather than rebased.
  git fetch origin "$GRAPH_BRANCH" >/dev/null 2>&1 || true
  git reset --hard "origin/$GRAPH_BRANCH" >/dev/null
  commit_or_exit
  attempt=$((attempt + 1))
  sleep 2
done

echo "update_data_file.sh: push failed after $MAX_RETRIES attempts." >&2
exit 1
