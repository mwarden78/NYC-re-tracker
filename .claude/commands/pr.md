---
description: Create a pull request for the current branch with proper labels and ticket linkage
---

# /pr - Create a pull request

Open a pull request for the current branch/worktree.

## Usage

```
/pr
/pr --draft
/pr --title "Custom title"
```

## What it does

1. Ensures all changes are committed
2. Pushes the branch to origin
3. Creates a PR using the PR template
4. Adds appropriate labels (risk level, type)
5. Links to the ticket in the PR description

## Instructions

When the user invokes `/pr`:

1. Check current branch name for ticket ID (e.g., `PROJ-123`)
2. Ensure changes are committed (prompt if uncommitted changes exist)
3. Push the branch: `git push -u origin <branch>`
4. Create PR:
   ```bash
   gh pr create \
     --title "PROJ-123: <ticket title or description>" \
     --body-file .github/PULL_REQUEST_TEMPLATE.md \
     --head <branch>
   ```
5. Add risk label based on changes (Low/Medium/High Risk)
6. Report the PR URL to the user

## Risk Assessment

- **Low Risk**: Docs, tests, minor UI tweaks, config changes
- **Medium Risk**: New features, bug fixes, refactoring
- **High Risk**: Auth, payments, database migrations, infrastructure

## Example

```bash
# From worktree or after identifying the branch
git push -u origin PROJ-123
gh pr create --title "PROJ-123: Add user authentication" --body "..."
gh pr edit <number> --add-label "Medium Risk"
```
