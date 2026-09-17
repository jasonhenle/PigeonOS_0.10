#!/usr/bin/env bash
# Open (and optionally merge) a release PR: experiment -> main.
# Run from anywhere; resolves the Pigeon git repo root automatically.

set -euo pipefail

SOURCE_BRANCH="${PIGEON_SHIP_SOURCE_BRANCH:-experiment}"
TARGET_BRANCH="main"
DO_MERGE=0
REPO="jasonhenle/PigeonOS_0.10"
VERSION_REL="PigeonOS_0.11_Development/Pigeon/pigeonSystem/pigeon/version.py"

usage() {
  cat <<'EOF'
Usage: ship_to_main.sh [--merge] [--source BRANCH]

  --merge          Merge the PR immediately after creating/finding it
  --source BRANCH  Source branch (default: experiment)

Environment:
  PIGEON_SHIP_SOURCE_BRANCH  Same as --source

Requires: git, gh (authenticated), clean enough tree to push.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --merge) DO_MERGE=1; shift ;;
    --source)
      SOURCE_BRANCH="${2:?missing branch after --source}"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! command -v gh >/dev/null 2>&1; then
  echo "ship_to_main: install GitHub CLI (brew install gh) and run: gh auth login" >&2
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ship_to_main: not inside a git repository" >&2
  exit 1
}
cd "$REPO_ROOT"

VERSION_FILE="$REPO_ROOT/$VERSION_REL"
if [[ ! -f "$VERSION_FILE" ]]; then
  echo "ship_to_main: version file not found: $VERSION_FILE" >&2
  exit 1
fi

VERSION="$(
  python3 - "$VERSION_FILE" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
fields = {m.group(1): int(m.group(2)) for m in re.finditer(r"^(MAJOR|MINOR|PATCH)\s*=\s*(\d+)\s*$", text, re.M)}
print(f"{fields['MAJOR']}.{fields['MINOR']}.{fields['PATCH']}")
PY
)"

echo "==> Pigeon release ship: ${SOURCE_BRANCH} -> ${TARGET_BRANCH} (v${VERSION})"

git fetch origin "$SOURCE_BRANCH" "$TARGET_BRANCH"

LOCAL_SHA="$(git rev-parse "$SOURCE_BRANCH")"
REMOTE_SHA="$(git rev-parse "origin/${SOURCE_BRANCH}" 2>/dev/null || true)"
if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
  echo "==> Pushing origin/${SOURCE_BRANCH}…"
  git push -u origin "$SOURCE_BRANCH"
else
  echo "==> origin/${SOURCE_BRANCH} already matches local"
fi

MAIN_SHA="$(git rev-parse "origin/${TARGET_BRANCH}")"
if git merge-base --is-ancestor "$LOCAL_SHA" "$MAIN_SHA" 2>/dev/null; then
  echo "Already released: ${SOURCE_BRANCH} is contained in origin/${TARGET_BRANCH} (${MAIN_SHA:0:7})."
  echo "Updates should already see v${VERSION} on main."
  exit 0
fi

EXISTING_PR="$(gh pr list --repo "$REPO" --head "$SOURCE_BRANCH" --base "$TARGET_BRANCH" --json number,url --jq '.[0].url // empty')"
if [[ -n "$EXISTING_PR" ]]; then
  PR_URL="$EXISTING_PR"
  echo "==> Using existing PR: $PR_URL"
else
  BODY="$(cat <<EOF
## Summary
Release **${VERSION}** from \`${SOURCE_BRANCH}\` into \`${TARGET_BRANCH}\`.

The in-app Updates button reads \`version.py\` on **main** only.

## Test plan
- [ ] \`curl\` raw \`version.py\` on main shows PATCH for ${VERSION}
- [ ] Pigeon Settings → Updates offers ${VERSION} after merge
EOF
)"
  PR_URL="$(gh pr create --repo "$REPO" --base "$TARGET_BRANCH" --head "$SOURCE_BRANCH" \
    --title "Release ${VERSION}" --body "$BODY")"
  echo "==> Opened PR: $PR_URL"
fi

if [[ "$DO_MERGE" -eq 1 ]]; then
  PR_NUM="$(gh pr view "$PR_URL" --repo "$REPO" --json number --jq .number)"
  gh pr merge "$PR_NUM" --repo "$REPO" --merge --delete-branch=false
  echo "==> Merged PR #${PR_NUM}. Updates will pick up v${VERSION} on main shortly."
else
  echo
  echo "Next: review and merge the PR, then check Updates in Pigeon."
  echo "  gh pr merge --repo ${REPO} --merge"
  echo "Or re-run: $0 --merge"
fi
