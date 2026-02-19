# Multi-Agent Coordination

When multiple AI agents (Claude sessions, Cursor instances, etc.) work on the same codebase simultaneously, conflicts can occur. This guide covers how to prevent them.

## The Problem

Without coordination, multiple agents can:
- Edit the same files simultaneously, causing merge conflicts
- Overwrite each other's uncommitted changes
- Create duplicate tickets for the same work
- Branch off stale versions of `main`

## Core Principle: Worktree Isolation

**Every agent MUST work in its own git worktree.**

A worktree gives each agent its own isolated filesystem state. Changes in one worktree don't affect another.

```bash
# Agent 1 creates worktree for PROJ-123
bin/vibe do PROJ-123
# Works in ../repo-worktrees/PROJ-123/

# Agent 2 creates worktree for PROJ-456
bin/vibe do PROJ-456
# Works in ../repo-worktrees/PROJ-456/

# No conflicts - completely isolated directories
```

### Why Not Just Use Different Branches?

Branches share the same working directory. If Agent 1 has uncommitted changes and Agent 2 switches branches, Agent 1's work is lost or corrupted. Worktrees prevent this.

## Situational Awareness Commands

Before starting work, understand what's in flight:

### See Active Feature Branches

```bash
git fetch --all
git branch -r | grep -v 'main\|HEAD\|origin/main'
```

Each branch typically represents another agent's work area.

### See Recent Activity

```bash
# Last 20 commits across all branches
git log --all --oneline --graph -20

# Commits in the last 24 hours
git log --all --oneline --since="24 hours ago"
```

### Check What Files Are Changing

```bash
# What files is branch X modifying?
git diff main...<branch-name> --name-only

# Example: see what PROJ-456 is changing
git diff main...PROJ-456 --name-only
```

### Check Tracker for In-Progress Work

```bash
bin/ticket list --status "In Progress"
```

If another agent has claimed a ticket, avoid that area.

## High-Risk Overlap Areas

These files are commonly edited and prone to conflicts:

| File/Area | Risk | Mitigation |
|-----------|------|------------|
| `CLAUDE.md` | High | Multiple agents updating docs |
| `package.json` | High | Dependency changes |
| `**/migrations/` | High | Use timestamps in filenames |
| `README.md` | Medium | Documentation updates |
| Shared components | Medium | Multiple features touching same UI |
| API routes | Medium | Multiple features adding endpoints |

### When Touching High-Risk Areas

1. **Pull latest main first** - Don't work off stale code
2. **Make changes quickly** - Don't leave uncommitted high-risk files
3. **Push immediately** - Get your changes in first
4. **Coordinate with user** - If you know another agent needs the same file

## File Conflict Prevention

### Check File History Before Editing

```bash
# See if other branches have recent changes to a file
git log --all --oneline -5 -- path/to/file.ts
```

If you see recent commits from other branches, that file is "hot" - be careful.

### Avoid Editing Files with Active Changes

If another branch has uncommitted or recent changes to a file:
- Prefer adding new files over modifying shared files
- Wait for the other work to merge
- Coordinate with the user to sequence the changes

### Keep Your Branch Up to Date

```bash
git fetch origin main
git rebase origin/main
```

Rebase regularly to avoid diverging too far from main. Long-lived branches are harder to merge.

## Communication Signals

Since agents cannot directly communicate, use these patterns:

### Branch Names Signal Scope

```
PROJ-123-auth-refactor     # Clearly working on auth
PROJ-456-add-dashboard     # Clearly working on dashboard
```

### Commit Messages List Affected Areas

```
PROJ-123: Refactor auth middleware

Files changed:
- lib/auth/middleware.ts
- lib/auth/session.ts
- app/api/auth/route.ts
```

### PR Descriptions Warn of Overlaps

```markdown
## Potential Overlaps

This PR modifies `lib/auth/` which may conflict with:
- PROJ-456 (also touching auth area)
```

### Tracker Status Signals Claimed Work

Keep tickets "In Progress" so other agents can see what's claimed:

```bash
bin/ticket update PROJ-123 --status "In Progress"
```

## Merge Conflict Resolution

If you encounter merge conflicts:

1. **Never force push** over another agent's commits
2. **Preserve both sets of changes** when resolving
3. **If conflicts are complex**, ask the user to coordinate
4. **Re-run tests** after resolving conflicts

## Pre-Work Checklist

Before starting any task:

- [ ] `git fetch --all` - Get latest remote state
- [ ] `git branch -r` - Check what branches exist
- [ ] `bin/ticket list --status "In Progress"` - Check claimed work
- [ ] Verify your branch is up to date with `main`
- [ ] Identify which files you'll modify
- [ ] Check those files aren't being actively modified elsewhere

## Anti-Patterns

### Working Without a Worktree

**Bad:** Multiple agents sharing the same directory, switching branches
**Good:** Each agent has its own worktree

### Long-Lived Branches

**Bad:** Branch diverges from main for days, massive merge conflict
**Good:** Small PRs, frequent rebases, merge within hours

### Touching Everything

**Bad:** One PR modifies 50 files across the entire codebase
**Good:** Focused PRs that touch one area

### Not Checking First

**Bad:** Start editing immediately without checking for conflicts
**Good:** Run situational awareness commands first
