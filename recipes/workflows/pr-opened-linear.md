# PR opened → Linear ticket status (In Review)

When a PR is opened, the workflow `.github/workflows/pr-opened.yml` automatically updates the associated Linear ticket to "In Review" status.

This is the companion to [pr-merge-linear.md](pr-merge-linear.md) which handles the `In Review → Deployed` transition when PRs are merged.

## Complete Linear Workflow

```
Backlog → In Progress → In Review → Deployed
           (manual)     (PR opened) (PR merged)
```

## Setup

1. **Repository secret**
   - Add `LINEAR_API_KEY` in the repo: **Settings → Secrets and variables → Actions → New repository secret**.
   - Use a Linear API key with write access (e.g. from [Linear → Settings → API](https://linear.app/settings/api)).

2. **Target state (optional)**
   - Default state name is **In Review**.
   - To use a different state name, add a repository **variable** (not secret): **Settings → Secrets and variables → Actions → Variables → New repository variable**:
     - Name: `LINEAR_IN_REVIEW_STATE`
     - Value: exact state name as in your Linear workflow.

3. **Branch naming**
   - The workflow only updates a ticket when the PR's **branch name** starts with a ticket ID (e.g. `PROJ-123` or `PROJ-123-add-feature`). If the branch doesn't match, the job does nothing.

## Behavior

- Runs on `pull_request` with `types: [opened, reopened]`.
- Extracts ticket ID from the PR head branch (pattern `^[A-Z]+-[0-9]+`).
- Looks up the issue in Linear by identifier, resolves the team's workflow state by name (case-insensitive), and updates the issue's state.
- If anything fails (no API key, ticket not found, state name not found), the job **logs a warning and exits successfully** so the PR creation is not blocked.

## Manual fallback

If the workflow isn't configured or you need to move a ticket manually:

```bash
bin/ticket update PROJ-123 --status "In Review"
```

Use the exact state name your Linear workflow uses.

## Related

- [pr-merge-linear.md](pr-merge-linear.md) - Updates ticket to Deployed when PR is merged
- [linear-setup.md](../tickets/linear-setup.md) - Initial Linear configuration
