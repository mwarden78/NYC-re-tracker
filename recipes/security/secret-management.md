# Secret Management

> **CRITICAL: Never commit `.env.local` or any file containing real secrets.**
> The `.env.local` file is gitignored by default. If yours isn't, fix your `.gitignore` immediately.

## When to Use This Recipe

Use this recipe when you need to:
- Understand what counts as a secret vs public config
- Set up secret management for a new project
- Sync secrets between local and deployed environments
- Configure the secrets allowlist

## Public vs Secret Values

### Public Configuration (safe to commit)
- Application settings (log level, feature flags)
- API endpoints (non-authenticated)
- Public keys (only the public half)
- Non-sensitive identifiers (workspace slugs)
- Build configuration

### Secrets (never commit)
- API keys with write access
- Database credentials
- Private keys
- Auth tokens (OAuth, JWT signing keys)
- Passwords
- Webhook secrets

### The Test
Ask: "Would exposing this value allow unauthorized access to systems or data?"
- Yes → It's a secret
- No → It's probably public config

## Secret Storage Strategy

### Local Development
```
.env.local          # Your secrets (gitignored)
.env.example        # Template with placeholder values (committed)
```

### CI/CD (GitHub Actions)
- Repository secrets for shared values
- Environment secrets for env-specific values
- Use `${{ secrets.NAME }}` syntax

### Production
- Use platform secret management:
  - Vercel: Environment Variables
  - Fly.io: `fly secrets set`
  - AWS: Secrets Manager or SSM Parameter Store

## File Hierarchy

```
.env.example        # Committed - template with placeholders
.env.local          # Gitignored - local secrets
.env.test           # Gitignored - test environment
.env.production     # NEVER CREATE - use platform secrets
```

## Allowlist Philosophy

Sometimes you need to commit values that look like secrets but aren't:
- Public API keys (read-only analytics)
- Test fixtures with fake credentials
- Example values in documentation

Use `.vibe/secrets.allowlist.json`:

```json
{
  "entries": [
    {
      "pattern": "pk_test_",
      "reason": "Stripe test mode public key",
      "added_by": "developer@example.com",
      "file_path": ".env.example"
    }
  ]
}
```

## Secret Rotation

When a secret is exposed:

1. **Revoke immediately** - Don't wait
2. **Generate new secret** - At the source
3. **Update all environments** - Local, CI, production
4. **Audit access** - Check for unauthorized use
5. **Document the incident** - For learning

## Syncing Secrets

Use the vibe CLI to sync:

```bash
# List what would be synced
bin/secrets sync .env.local --provider github --dry-run

# Sync to GitHub Actions
bin/secrets sync .env.local --provider github
```

## Best Practices

1. **Use environment-specific secrets** - Don't share between dev/staging/prod
2. **Rotate regularly** - At least on team member departures
3. **Audit access** - Know who can access what
4. **Prefer short-lived tokens** - OAuth refresh tokens > long-lived API keys
5. **Use the principle of least privilege** - Only grant what's needed

## Common Mistakes

### Accidentally Committing Secrets

**Symptoms:**
- CI fails with "secret detected" error
- Gitleaks blocks your push
- Someone messages you about exposed credentials

**Fix:**
1. Remove the secret from the file
2. Commit the removal
3. **Rotate the secret immediately** - assume it's compromised
4. If needed, add to `.vibe/secrets.allowlist.json` (only for false positives)

### .env.local Not Gitignored

**Check:**
```bash
git check-ignore .env.local
# Should output: .env.local
```

**Fix (if not gitignored):**
```bash
echo ".env.local" >> .gitignore
git add .gitignore
git commit -m "Ensure .env.local is gitignored"
```

### Using .env Instead of .env.local

The `.env` file is NOT automatically gitignored in many setups. Always use `.env.local` for secrets.

**Pattern:**
- `.env` - Can be committed (non-secret defaults)
- `.env.local` - Never committed (your secrets)
- `.env.example` - Always committed (template)

## Linear API Key Safety

When setting up Linear integration:

1. **Add the key to `.env.local`, not `.env`:**
   ```bash
   echo "LINEAR_API_KEY=lin_api_xxxxx" >> .env.local
   ```

2. **Never pass the key in command arguments:**
   ```bash
   # BAD - key visible in shell history
   LINEAR_API_KEY=xxx bin/ticket list

   # GOOD - key in .env.local
   bin/ticket list
   ```

3. **Use GitHub secrets for CI:**
   - Go to Settings → Secrets → Actions
   - Add `LINEAR_API_KEY` as a repository secret

## Extension Points

- Add secret providers for your platforms
- Configure automated rotation
- Set up secret scanning alerts
