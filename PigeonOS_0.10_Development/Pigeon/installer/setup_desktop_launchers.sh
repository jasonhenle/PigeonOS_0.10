#!/usr/bin/env bash
# Create side-by-side git worktrees + Desktop double-click launchers:
#   Pigeon (experiment).command  -> ~/Desktop/Pigeon  (PigeonOS_0.10 branch)
#   Pigeon (0.10).command        -> same as experiment
#   Pigeon (main).command        -> ~/Desktop/Pigeon-main (main branch)
#
# Run once (or again after moving the repo):
#   bash installer/setup_desktop_launchers.sh

set -euo pipefail

DESKTOP="${HOME}/Desktop"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "setup_desktop_launchers: run from inside the Pigeon git repo" >&2
  exit 1
}

DEV_BRANCH="PigeonOS_0.10"
DEV_ROOT="${DESKTOP}/Pigeon"
MAIN_ROOT="${DESKTOP}/Pigeon-main"
DEV_APP="${DEV_ROOT}/PigeonOS_0.10_Development/Pigeon"
MAIN_APP="${MAIN_ROOT}/PigeonOS_0.10_Development/Pigeon"
LAUNCHER_EXPERIMENT="${DESKTOP}/Pigeon (experiment).command"
LAUNCHER_MAIN="${DESKTOP}/Pigeon (main).command"
LAUNCHER_ALIAS="${DESKTOP}/Pigeon (0.10).command"

echo "==> Pigeon desktop launchers"
echo "    repo:   ${REPO_ROOT}"
echo "    dev:    ${DEV_APP}  (branch ${DEV_BRANCH})"
echo "    main:   ${MAIN_APP}"

cd "${REPO_ROOT}"
git fetch origin "${DEV_BRANCH}" main 2>/dev/null || git fetch origin main || true

# Prefer keeping the caller's worktree on its current branch; only switch Desktop/Pigeon.
if [[ -d "${DEV_ROOT}/.git" ]] || [[ -f "${DEV_ROOT}/.git" ]]; then
  echo "==> Ensuring ${DEV_BRANCH} in ${DEV_ROOT}"
  git -C "${DEV_ROOT}" fetch origin "${DEV_BRANCH}" 2>/dev/null || true
  git -C "${DEV_ROOT}" checkout "${DEV_BRANCH}" 2>/dev/null \
    || git -C "${DEV_ROOT}" checkout -b "${DEV_BRANCH}" "origin/${DEV_BRANCH}" 2>/dev/null \
    || echo "    (could not check out ${DEV_BRANCH} in ${DEV_ROOT} — continue)"
  git -C "${DEV_ROOT}" pull --ff-only origin "${DEV_BRANCH}" 2>/dev/null || true
fi
if [[ -d "${MAIN_ROOT}/.git" ]] || [[ -f "${MAIN_ROOT}/.git" ]]; then
  echo "==> Updating main worktree at ${MAIN_ROOT}"
  git -C "${MAIN_ROOT}" fetch origin main
  git -C "${MAIN_ROOT}" checkout main
  git -C "${MAIN_ROOT}" pull --ff-only origin main || true
else
  echo "==> Creating main worktree at ${MAIN_ROOT}"
  git worktree add -B main "${MAIN_ROOT}" origin/main
fi

write_launcher() {
  local path="$1"
  local label="$2"
  local root="$3"
  local app="$4"
  local branch="$5"
  cat >"${path}" <<EOF
#!/bin/bash
set -euo pipefail

ROOT="${root}"
APP="${app}"
BRANCH="${branch}"
LAUNCHER="${label}"

if [[ ! -f "\${APP}/installer/run_pigeon_0_10.command" ]]; then
  osascript -e "display alert \"Pigeon not found\" message \"Expected install at:\${APP}\" as critical"
  exit 1
fi

cd "\${ROOT}" || exit 1
VERSION="unknown"
VP="\${APP}/pigeonSystem/pigeon/version.py"
if [[ -f "\${VP}" ]]; then
  VERSION="\$(grep -E '^PATCH = ' "\${VP}" | head -1 | sed 's/PATCH = //')"
  MINOR="\$(grep -E '^MINOR = ' "\${VP}" | head -1 | sed 's/MINOR = //')"
  MAJOR="\$(grep -E '^MAJOR = ' "\${VP}" | head -1 | sed 's/MAJOR = //')"
  VERSION="\${MAJOR}.\${MINOR}.\${VERSION}"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Pigeon — \${LAUNCHER}"
echo "  branch \${BRANCH}  ·  v\${VERSION}"
echo "  \${APP}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exec bash "\${APP}/installer/run_pigeon_0_10.command"
EOF
  chmod +x "${path}"
  echo "==> Wrote ${path}"
}

write_launcher "${LAUNCHER_EXPERIMENT}" "PigeonOS_0.10 (dev)" "${DEV_ROOT}" "${DEV_APP}" "${DEV_BRANCH}"
write_launcher "${LAUNCHER_MAIN}" "main (release)" "${MAIN_ROOT}" "${MAIN_APP}" "main"
write_launcher "${LAUNCHER_ALIAS}" "PigeonOS_0.10 (dev)" "${DEV_ROOT}" "${DEV_APP}" "${DEV_BRANCH}"

echo
echo "Done. Double-click on your Desktop:"
echo "  • Pigeon (experiment).command  — ${DEV_BRANCH} / PigeonOS 0.10"
echo "  • Pigeon (0.10).command        — same as experiment (explicit 0.10 name)"
echo "  • Pigeon (main).command        — same code the Updates button ships"
echo
echo "Re-run this script anytime to refresh the main worktree or recreate launchers."
