# Grandfathered Tickets

This file tracks tickets and patterns that existed before this boilerplate was adopted. These tickets may not follow current conventions but are recognized as valid.

## Supported Grandfathered Patterns

The PR policy workflow (`.github/workflows/pr-policy.yml`) recognizes these legacy patterns in addition to standard patterns:

### Ticket References
- `[LEGACY-123]` - Square bracket legacy format
- `BACKLOG-123` - Backlog prefix format
- Numeric prefixes (e.g., `123-description`)

### Branch Names
- `feature/description` - GitFlow feature branches
- `bugfix/description` - GitFlow bugfix branches
- `hotfix/description` - GitFlow hotfix branches
- `fix/description` - Short fix branches
- `chore/description` - Maintenance branches

## Adding Custom Patterns

To add project-specific grandfathered patterns:

1. Edit `.github/workflows/pr-policy.yml`
2. Find the `grandfatheredPatterns` array in the "Check ticket reference" step
3. Add your regex pattern:

```javascript
const grandfatheredPatterns = [
  /\[LEGACY-\d+\]/i,      // Existing
  /YOUR-PATTERN-\d+/,     // Add your pattern here
];
```

## Grandfathered Ticket Registry

Track individual grandfathered tickets here for documentation purposes:

### Format

```markdown
## [TICKET-ID]
- **Original Title**: The original ticket title
- **Date Grandfathered**: YYYY-MM-DD
- **Reason**: Why this ticket doesn't follow conventions
- **Notes**: Any additional context
```

### Entries

<!-- Add grandfathered tickets below this line -->

---

## Related

- `recipes/tickets/ticket-audit-and-grandfathering.md` - Full grandfathering process
- `.github/workflows/pr-policy.yml` - PR policy with pattern matching
