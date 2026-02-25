---
description: Clean up worktrees for branches that have been merged
---

# /cleanup - Clean up merged worktrees

Remove worktrees for branches that have been merged.

## Usage

```
/cleanup
/cleanup --dry-run
/cleanup --force
```

## What it does

1. Lists all worktrees (`git worktree list`)
2. For each worktree (except main repo):
   - Check if the branch has been merged to main
   - Check if there's an open PR for the branch
3. Remove worktrees for merged branches
4. Delete the local branches
5. Update `.vibe/local_state.json`
6. Run `git worktree prune` to clean up stale entries

## Instructions

When the user invokes `/cleanup`:

1. Run `git worktree list` to see all worktrees
2. For each non-main worktree, check merge status:
   ```bash
   git branch --merged main | grep <branch>
   ```
3. If merged and no open PR, remove:
   ```bash
   git worktree remove <path>
   git branch -d <branch>
   ```
4. Run `bin/vibe doctor` to sync state
5. Report what was cleaned up

## Safety checks

- Never remove worktrees with uncommitted changes (unless `--force`)
- Never remove worktrees with open PRs
- Always confirm before removing (unless `--force`)

## Example

```
Cleanup Results
===============

Removed:
  - ../project-worktrees/PROJ-123 (merged in PR #45)
  - ../project-worktrees/PROJ-124 (merged in PR #47)

Kept (unmerged):
  - ../project-worktrees/PROJ-125 (open PR #48)

Kept (uncommitted changes):
  - ../project-worktrees/PROJ-126

Cleaned up 2 worktrees.
```

## Related

- `/doctor` - Check for stale worktrees
- `/do` - Create new worktrees
