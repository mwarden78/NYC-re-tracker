# Linear Label IDs

When using the Linear API directly (e.g., in hooks or scripts), label IDs are more reliable than label names.

## Why Use Label IDs

- **Names can change** - Renaming "Bug" to "Bugs" breaks name-based lookups
- **Names may not be unique** - Different teams might have labels with the same name
- **API efficiency** - Direct ID lookup is faster than name resolution

## Get Your Label IDs

```bash
bin/ticket labels
```

Output:
```
Labels:
------------------------------------------------------------
  Backend                        abc123-def456-789...
  Bug                            xyz789-abc123-456...
  Feature                        def456-xyz789-123...
  Frontend                       789abc-def456-xyz...
```

For JSON output (useful for scripting):
```bash
bin/ticket labels --json
```

## Store Label IDs for Reference

Create a reference file in your project (e.g., `.claude/commands/linear-create.md` or a local notes file):

```markdown
# Linear Label IDs

| Label | ID |
|-------|-----|
| Bug | `abc123-def456-789...` |
| Feature | `def456-xyz789-123...` |
| Backend | `abc123-def456-789...` |
| Frontend | `789abc-def456-xyz...` |
```

## Using Label IDs in API Calls

### In curl/scripts

```bash
curl -X POST https://api.linear.app/graphql \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "mutation { issueCreate(input: { teamId: \"TEAM_ID\", title: \"Bug fix\", labelIds: [\"abc123-def456-789...\"] }) { success issue { id } } }"
  }'
```

### In the hook scripts

The boilerplate's hook scripts use state type resolution rather than label IDs, but you can extend them to add labels when creating tickets.

## GraphQL Query Reference

To fetch labels directly:

```graphql
query {
  issueLabels(first: 100) {
    nodes {
      id
      name
      color
    }
  }
}
```

To filter by team:

```graphql
query {
  issueLabels(filter: { team: { id: { eq: "YOUR_TEAM_ID" } } }) {
    nodes {
      id
      name
    }
  }
}
```

## Related

- [linear-setup.md](linear-setup.md) - Initial Linear configuration
- [creating-tickets.md](creating-tickets.md) - Ticket creation guidance
