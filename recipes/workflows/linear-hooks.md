# Linear Hooks for Automatic Ticket Updates

Claude Code hooks can automatically update Linear ticket status as you work, providing a smoother experience than CI-only automation.

## What the Hooks Do

| Hook | Trigger | Action |
|------|---------|--------|
| `linear-start-ticket.sh` | User prompt contains ticket ID | Marks ticket as "In Progress" |
| `linear-update-on-commit.sh` | Git commit with ticket ID | Marks ticket as "Done" |

## Complete Workflow

```
Backlog → In Progress → Done
           (hook)       (hook)
```

With CI workflows:
```
Backlog → In Progress → In Review → Deployed
           (hook)       (PR open)   (PR merge)
```

## Setup

### 1. Add API Key to .env.local

```bash
# .env.local (gitignored)
LINEAR_API_KEY=lin_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Get your API key from [Linear Settings → API](https://linear.app/settings/api).

### 2. Copy Example Configuration

```bash
cp .claude/settings.local.json.example .claude/settings.local.json
```

### 3. Verify Scripts are Executable

```bash
chmod +x .claude/hooks/*.sh
```

## Configuration

### Custom Ticket Pattern

By default, hooks match the pattern `[A-Z]+-[0-9]+` (e.g., `PROJ-123`, `CITY-456`).

To use a custom pattern, add to `.env.local`:

```bash
TICKET_PATTERN='MYPROJ-[0-9]+'
```

## How It Works

### Start Ticket Hook

When you mention a ticket ID in your prompt (e.g., "Work on PROJ-123"), the hook:

1. Extracts the ticket ID from your message
2. Checks the ticket's current state
3. If not already started/completed, updates to "In Progress"
4. Runs asynchronously (doesn't block your prompt)

**Smart behavior:**
- Won't downgrade status (Done → In Progress)
- Silently exits if no API key configured
- Works with any Linear team's workflow states

### Commit Hook

After any `git commit` command, the hook:

1. Extracts ticket ID from the commit message
2. Finds the "Done" state for the team
3. Updates the ticket status
4. Prints confirmation

## Customization

### Different Target States

Edit the hooks to find different state names. For example, to use "To Test" instead of "Done":

```python
# In linear-update-on-commit.sh
if state.get('type') == 'completed' and 'test' in state.get('name', '').lower():
```

### Add More Hooks

You can add hooks for other events:

```json
{
  "PostToolUse": [
    {
      "matcher": "Bash(git push*)",
      "hooks": [
        {
          "type": "command",
          "command": ".claude/hooks/my-custom-hook.sh"
        }
      ]
    }
  ]
}
```

## Troubleshooting

### Hooks Not Running

1. Check `.claude/settings.local.json` exists and is valid JSON
2. Verify scripts are executable: `ls -la .claude/hooks/`
3. Check Claude Code loaded the hooks (restart may be needed)

### API Errors

1. Verify `LINEAR_API_KEY` is set in `.env.local`
2. Check the key has write permissions
3. Test manually: `LINEAR_API_KEY=xxx .claude/hooks/linear-start-ticket.sh <<< "PROJ-123"`

### Wrong State Names

Linear teams can have custom workflow states. The hooks look for:
- "In Progress": Any state with `type: started` and "progress" in the name
- "Done": Any state with `type: completed` and name "Done"

If your team uses different names, edit the Python parsing in the hook scripts.

## Related

- [pr-opened-linear.md](pr-opened-linear.md) - CI workflow for PR opened → In Review
- [pr-merge-linear.md](pr-merge-linear.md) - CI workflow for PR merged → Deployed
- [linear-setup.md](../tickets/linear-setup.md) - Initial Linear configuration
