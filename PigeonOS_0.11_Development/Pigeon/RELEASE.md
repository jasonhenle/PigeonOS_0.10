# Releasing Pigeon (0.7.x)

The **Settings → Updates** button downloads from GitHub branch **`main`**.  
Work on **`experiment`**, then merge into **`main`** when you want Mac/Pi installs to see a new version.

## Quick reference

```text
code → bump PATCH in version.py → commit → push experiment → PR → merge main → Updates shows new version
```

Version file: `pigeonSystem/pigeon/version.py`

---

## Daily development

```bash
cd /Users/jasonhenley/Desktop/Pigeon
git checkout experiment
git pull origin experiment

# … edit code …
# bump PATCH in pigeonSystem/pigeon/version.py

git add -A   # or add specific files
git commit -m "Describe the fix (0.7.NN)."
git push -u origin experiment
```

Nothing is released yet — `experiment` is your sandbox on GitHub.

---

## Ship a release (one command)

From the **git repo root** (`Desktop/Pigeon`):

```bash
./PigeonOS_0.11_Development/Pigeon/installer/ship_to_main.sh
```

This script:

1. Reads the version from `version.py`
2. Pushes `experiment` to GitHub
3. Opens a pull request: `experiment` → `main`
4. Asks before merging (or pass `--merge` to merge immediately)

After merge, wait ~1 minute for GitHub’s cache, then **Updates** in Pigeon should offer the new version.

---

## Test both branches on your Mac (Desktop launchers)

Run once from the git repo root:

```bash
./PigeonOS_0.11_Development/Pigeon/installer/setup_desktop_launchers.sh
```

This creates two double-click icons on your Desktop:

| Icon | Folder | Branch |
|------|--------|--------|
| **Pigeon (experiment).command** | `~/Desktop/Pigeon/…` | `experiment` (dev) |
| **Pigeon (main).command** | `~/Desktop/Pigeon-main/…` | `main` (what Updates ships) |

Each uses a **separate copy** of the repo (git worktree), so you can run either without switching branches. The Terminal window prints the branch and version before Pigeon starts.

Re-run the setup script after moving the repo or to refresh the main copy from GitHub.

## Ship manually (step by step)

### 1. Push your branch

```bash
git checkout experiment
git push -u origin experiment
```

### 2. Open a pull request

GitHub web UI: **Compare & pull request** from `experiment` into `main`.

Or CLI:

```bash
gh pr create \
  --base main \
  --head experiment \
  --title "Release 0.7.85" \
  --body "$(cat <<'EOF'
## Summary
- …

## Test plan
- [ ] Updates button shows 0.7.85 after merge
- [ ] …
EOF
)"
```

### 3. Merge the PR

GitHub UI: **Merge pull request** (merge commit is fine).

Or CLI:

```bash
gh pr merge --merge
```

Do **not** delete `experiment` — keep using it for the next round.

### 4. Verify

```bash
gh api repos/jasonhenle/PigeonOS_0.10/commits/main --jq '.sha[0:7]'
curl -s "https://raw.githubusercontent.com/jasonhenle/PigeonOS_0.10/main/PigeonOS_0.11_Development/Pigeon/pigeonSystem/pigeon/version.py" | grep PATCH
```

Open Pigeon → Settings → **Check for updates**. You should see a newer version than the one installed.

---

## What is a PR?

A **pull request** is a proposal: “merge these commits from branch A into branch B.”

- You keep coding on `experiment` without touching the release line.
- You review the diff once before it becomes `main`.
- The in-app updater only cares about `main`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|--------|-----|
| Updates says “up to date” on old version | Changes only on `experiment` | Merge PR into `main` |
| Updates still old after merge | GitHub raw cache / app not restarted | Wait 1–2 min, check Updates again |
| Wrong version on GitHub | Forgot to bump `PATCH` | Bump, commit, new PR |

---

## Optional: protect `main`

In GitHub → **Settings → Branches → Add rule** for `main`:

- Require a pull request before merging
- (Optional) Require status checks if you add CI later

That prevents accidental direct pushes to the release branch.
