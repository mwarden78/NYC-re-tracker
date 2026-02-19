# Shortcut Setup

Set up Shortcut.com as your ticket tracking system.

## Prerequisites

- Shortcut account with API access
- Admin or member permissions to generate API tokens

## Setup Steps

### 1. Generate an API Token

1. Go to [Shortcut Settings → API Tokens](https://app.shortcut.com/settings/account/api-tokens)
2. Click **Generate Token**
3. Give it a descriptive name (e.g., "Vibe Code Boilerplate")
4. Copy the token (you won't see it again)

### 2. Configure Environment

Add the token to your local environment:

```bash
# Add to .env.local (gitignored)
echo "SHORTCUT_API_TOKEN=your-token-here" >> .env.local
```

### 3. Run Setup Wizard

```bash
bin/vibe setup --wizard tracker
```

Select **Shortcut** when prompted.

### 4. Configure GitHub Actions (Optional)

For automatic ticket status updates on PR events:

1. Add repository secret `SHORTCUT_API_TOKEN`:
   - Settings → Secrets and variables → Actions → New repository secret

2. Update workflow files to use Shortcut:

```yaml
# .github/workflows/pr-merged.yml
env:
  SHORTCUT_API_TOKEN: ${{ secrets.SHORTCUT_API_TOKEN }}
```

## Configuration

In `.vibe/config.json`:

```json
{
  "tracker": {
    "type": "shortcut",
    "config": {
      "deployed_state": "Done"
    }
  }
}
```

## Branch Naming

Shortcut story IDs are numeric. Branch naming convention:

```
SC-12345          # Shortcut story ID
SC-12345-feature  # With description
```

The `SC-` prefix is optional but recommended for clarity.

## CLI Commands

```bash
# List stories
bin/ticket list

# Get story details
bin/ticket get SC-12345
bin/ticket get 12345

# Create story
bin/ticket create "Add login button" --labels "Feature,Frontend"

# Update story status
bin/ticket update SC-12345 --status "Done"

# List labels
bin/ticket labels
```

## Workflow States

Common Shortcut workflow states:

| State | Description |
|-------|-------------|
| Backlog | Not started |
| Unstarted | Ready to start |
| Started | In progress |
| Ready for Review | PR opened |
| Done | Completed |

Map these to your workflow in `.vibe/config.json`:

```json
{
  "tracker": {
    "type": "shortcut",
    "config": {
      "in_review_state": "Ready for Review",
      "deployed_state": "Done"
    }
  }
}
```

## Shortcut Native GitHub Integration

Shortcut offers built-in GitHub integration:

1. In Shortcut: Settings → Integrations → GitHub
2. Connect your GitHub organization
3. Branches with story IDs auto-link

This works alongside the boilerplate's status updates.

## Story Types

Shortcut supports different story types:

- **Feature** - New functionality
- **Bug** - Something broken
- **Chore** - Maintenance, cleanup

When creating tickets via CLI, specify type:

```bash
bin/ticket create "Fix login issue" --type bug
```

## Troubleshooting

### "SHORTCUT_API_TOKEN not set"

Ensure the token is in your environment:

```bash
# Check if set
echo $SHORTCUT_API_TOKEN

# Set for current session
export SHORTCUT_API_TOKEN=your-token
```

### "Story not found"

- Verify the story ID is correct
- Ensure your token has access to the workspace
- Check if the story was archived

### "Invalid workflow state"

Workflow state names must match exactly. List available states:

```bash
# This will show the states for your workspace
bin/ticket list  # Look at status column for valid values
```

## Related

- [linear-setup.md](linear-setup.md) - Alternative: Linear configuration
- [creating-tickets.md](creating-tickets.md) - Ticket creation best practices
- [../workflows/pr-merge-linear.md](../workflows/pr-merge-linear.md) - Automatic status updates
