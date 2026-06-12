#!/usr/bin/env bash
# update_data_file.sh — clone the dependency-graph repo, copie le JSON dans
# data/<owner>_<repo>.json, commit et push avec retry sur conflit.
#
# Usage : update_data_file.sh --repo owner/repo --data-json /tmp/deps.json --token <pat>
set -euo pipefail

REPO=""
DATA_JSON=""
TOKEN=""
GRAPH_REPO_URL="${GRAPH_REPO_URL:-https://codeberg.org/Vidocq/dep-graph.git}"
GRAPH_BRANCH="${GRAPH_BRANCH:-main}"
MAX_RETRIES=5

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)       REPO="$2"; shift 2 ;;
    --data-json)  DATA_JSON="$2"; shift 2 ;;
    --token)      TOKEN="$2"; shift 2 ;;
    *) echo "Argument inconnu : $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$REPO" || -z "$DATA_JSON" || -z "$TOKEN" ]]; then
  echo "Usage: $0 --repo owner/repo --data-json /tmp/deps.json --token <pat>" >&2
  exit 2
fi

if [[ ! -f "$DATA_JSON" ]]; then
  echo "update_data_file.sh: fichier introuvable: $DATA_JSON" >&2
  exit 1
fi

# Validation préalable : vérifie que le JSON est parsable et que le champ "repo" matche.
python3 -c "
import json, sys
d = json.load(open('$DATA_JSON'))
assert d.get('repo') == '$REPO', f'repo mismatch: {d.get(\"repo\")} vs $REPO'
assert isinstance(d.get('produces'), list)
assert isinstance(d.get('consumes'), list)
" || { echo "update_data_file.sh: JSON invalide" >&2; exit 1; }

# slug : owner/repo -> owner_repo
SLUG="${REPO/\//_}"
DATA_FILE="data/${SLUG}.json"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# URL avec auth basic (token comme password, user "vidocq-bot" placeholder)
AUTH_URL="${GRAPH_REPO_URL/https:\/\//https://vidocq-bot:${TOKEN}@}"

git clone --depth 1 --branch "$GRAPH_BRANCH" "$AUTH_URL" "$WORK/repo" >/dev/null 2>&1
cd "$WORK/repo"
git config user.name "vidocq-bot"
git config user.email "bot@vidocq.dev"

mkdir -p data
cp "$DATA_JSON" "$DATA_FILE"

if git diff --quiet -- "$DATA_FILE"; then
  if ! git status --porcelain | grep -q "$DATA_FILE"; then
    echo "update_data_file.sh: aucun changement, rien à committer."
    exit 0
  fi
fi

git add "$DATA_FILE"
git commit -m "chore(graph): update ${REPO} [skip ci]" >/dev/null

attempt=1
while (( attempt <= MAX_RETRIES )); do
  if git push origin "$GRAPH_BRANCH" 2>/tmp/push.err; then
    echo "update_data_file.sh: push OK ($DATA_FILE) tentative $attempt"
    exit 0
  fi
  echo "update_data_file.sh: push tentative $attempt échouée, rebase..." >&2
  cat /tmp/push.err >&2
  git fetch origin "$GRAPH_BRANCH" >/dev/null 2>&1 || true
  if ! git rebase "origin/$GRAPH_BRANCH"; then
    # En cas de conflit (peu probable, on touche un seul fichier), on prend notre version.
    git checkout --theirs "$DATA_FILE" 2>/dev/null || true
    git add "$DATA_FILE"
    git rebase --continue || git rebase --skip || true
  fi
  attempt=$((attempt + 1))
  sleep 2
done

echo "update_data_file.sh: push échoué après $MAX_RETRIES tentatives." >&2
exit 1
