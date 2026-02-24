# /setup - Initial Project Setup

Run the setup wizard to configure your vibe coding project.

## Usage

```
/setup
/setup --wizard tracker
/setup --wizard github
/setup --force
```

## What it does

1. Auto-detects GitHub from `gh` CLI and git remote
2. Configures ticket tracker (Linear or Shortcut)
3. Sets up branch naming conventions
4. Creates PR template if missing
5. Initializes local state for worktree tracking

## Instructions

When the user invokes `/setup`:

1. Run `bin/vibe setup` (or with flags)
2. Guide the user through any interactive prompts
3. After completion, remind them to:
   - Update the Project Overview in CLAUDE.md
   - Add LINEAR_API_KEY to .env.local (if using Linear)
   - Enable Linear GitHub integration in Linear Settings
   - Run `/doctor` to verify configuration
