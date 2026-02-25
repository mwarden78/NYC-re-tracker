---
description: Run the initial project setup wizard to configure Linear, GitHub, and other integrations
---

# /setup - Initial project setup

Run the setup wizard to configure the project for vibe coding.

## Usage

```
/setup
/setup --wizard tracker    # Run only the tracker wizard
/setup --wizard github     # Run only the GitHub wizard
/setup --force             # Reconfigure even if already set up
```

## What it does

1. **Auto-detects GitHub** from `gh` CLI and git remote
2. **Configures ticket tracker** (Linear or Shortcut)
3. **Sets up branch naming** conventions
4. **Creates PR template** if missing
5. **Initializes local state** for worktree tracking

## Instructions

When the user invokes `/setup`:

1. Run `bin/vibe setup` (or with the specified options)
2. Guide user through any prompts
3. After completion, remind them to:
   - Update the Project Overview section in CLAUDE.md
   - Add LINEAR_API_KEY to .env.local if using Linear
   - Enable the Linear GitHub integration for automatic status updates
4. Run `/doctor` to verify everything is configured correctly

## After setup

**Critical next steps:**

1. **Update CLAUDE.md** - Fill in the Project Overview section:
   - What this project does
   - Tech stack
   - Key features

2. **Add Linear API key** (if using Linear):
   ```bash
   echo "LINEAR_API_KEY=lin_api_xxxxx" >> .env.local
   ```

3. **Enable Linear GitHub integration**:
   - Go to Linear Settings → Integrations → GitHub
   - Connect your GitHub account
   - Enable auto-status updates for PRs

4. **Verify setup**:
   ```
   /doctor
   ```

## Example

```bash
bin/vibe setup

# Output:
# GitHub: kdenny/my-project (auto-detected)
# Tracker: Linear (team: MY-TEAM)
# Branch pattern: {PROJ}-{num}
#
# Setup complete! Next steps:
# 1. Update CLAUDE.md Project Overview
# 2. Add LINEAR_API_KEY to .env.local
# 3. Run /doctor to verify
```
