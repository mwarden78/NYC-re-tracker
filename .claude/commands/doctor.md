# /doctor - Check project health

Run comprehensive health checks on the project configuration, integrations, and GitHub Actions.

## Usage

```
/doctor
/doctor --verbose
/doctor --github-actions    # Also check GitHub secrets and workflow runs
```

## What it checks

### Core Checks
- Valid `.vibe/config.json`
- Python version (3.11+)
- Git installation and repository
- GitHub CLI (`gh`) authentication
- Gitignore entries

### Integrations
- **Tracker**: Linear or Shortcut configuration + API key
- **PromptVault**: API key for LLM apps
- **Fly.io**: CLI authentication
- **Vercel**: CLI authentication
- **Supabase**: CLI or environment variables
- **Local hooks**: Claude Code hooks configuration

### GitHub Actions (with `--github-actions`)
- Required secrets (LINEAR_API_KEY, etc.)
- Recent workflow run status

## Instructions

When the user invokes `/doctor`:

1. Run `bin/vibe doctor` (or with flags)
2. Report results grouped by category
3. For warnings/failures, show fix hints
4. Summarize: X/Y passed, N warnings, M skipped

## Status Icons

| Icon | Status | Meaning |
|------|--------|---------|
| ✓ | PASS | Check passed |
| ⚠ | WARN | Warning - should address |
| ✗ | FAIL | Failed - must fix |
| ○ | SKIP | Skipped (optional/not configured) |
