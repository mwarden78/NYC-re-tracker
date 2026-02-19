# HUMAN Label Usage

## When to Use This Recipe

Use this recipe when you need to:
- Understand when to mark tickets as requiring human intervention
- Define boundaries for AI agent capabilities
- Ensure critical decisions get human review

## The HUMAN Label

The `HUMAN` label means **"I cannot proceed without human action."** Use it sparingly.

## When to Apply HUMAN

**Ask yourself:** "Can I do this programmatically?" If yes, do it. If no, create a HUMAN ticket.

### DO Create HUMAN Tickets For

**Obtaining actual secret values:**
- API keys that need to be retrieved from a service
- Database passwords the human must generate or copy
- OAuth credentials from third-party dashboards

**External account actions:**
- Creating accounts on third-party services (Stripe, AWS, etc.)
- Enabling billing or paid features
- Accepting terms of service on behalf of the organization

**Subjective decisions:**
- UI/UX design choices without clear requirements
- Brand voice and copy approval
- Feature prioritization decisions
- Trade-offs where the human's preference matters

**Legal/compliance:**
- Terms of service changes
- Privacy policy updates
- Contract-related decisions

**External communications:**
- Emails to customers
- Public announcements
- Support responses

### DO NOT Create HUMAN Tickets For

**Writing code or config (even security-related):**
- RLS policies, auth middleware, encryption → write the code
- Dockerfiles, CI/CD workflows → write the config
- Database migrations → write and run them

**Running CLI commands:**
- `fly secrets set KEY=value` → run it
- `gh secret set NAME` → run it
- `npm install` → run it

**Setting up infrastructure:**
- Creating files, directories, configs
- Writing terraform/pulumi/CDK
- Configuring deployment pipelines

**Documentation:**
- README updates, API docs, code comments

**Architecture decisions (when requirements are clear):**
- "Use PostgreSQL for the database" → implement it
- "Add authentication with JWT" → implement it
- Don't create HUMAN tickets for implementation details

## How to Use HUMAN

### In Tickets
```markdown
Title: [HUMAN] Review and approve new authentication flow

The authentication flow changes are implemented. Requires human review for:
- Security implications
- UX flow approval
- Error message copy
```

### In Code Comments
```python
# HUMAN: This retry logic needs review. Is 5 retries appropriate
# for our use case? Too many could mask underlying issues.
```

### In PR Descriptions
```markdown
## Requires Human Review

- [ ] Security team: Review token handling
- [ ] Product: Approve user-facing messages
- [ ] Legal: Verify GDPR compliance of data handling
```

## Workflow with HUMAN Label

```
Agent creates ticket with HUMAN label
         ↓
Ticket appears in human review queue
         ↓
Human makes decision or takes action
         ↓
Human removes HUMAN label and updates ticket
         ↓
Agent can proceed with implementation
```

## Agent Behavior

When an agent encounters a HUMAN-labeled task:

1. **Don't attempt to complete it** - Wait for human input
2. **Provide context** - Explain why human input is needed
3. **Suggest options** - If possible, provide choices for human
4. **Continue other work** - Work on non-blocked tasks

### Example Agent Response
```
This task requires human decision-making:

Reason: Security implications of new API scope

Options I've identified:
1. Read-only scope (safer, limited functionality)
2. Read-write scope (full functionality, higher risk)
3. Separate scopes per feature (complex, most flexible)

I'll proceed with other tasks while awaiting your decision.
```

## Review Queue

Maintain a view of HUMAN-labeled items:

```bash
# In Linear
Filter: Label = HUMAN AND Status != Done

# In GitHub
Label: HUMAN
is:open
```

## Best Practices

1. **Be specific** - Say exactly what human input is needed
2. **Provide context** - Explain why agent can't proceed
3. **Suggest next steps** - Make it easy for human to act
4. **Time-box reviews** - Don't let HUMAN items languish
5. **Document decisions** - Record why decisions were made

## Anti-Patterns

### Overuse (Most Common Problem)
Don't create HUMAN tickets for things you can do programmatically:
- "Configure secrets in Fly.io" → Just run `fly secrets set`
- "Write authentication middleware" → Just write the code
- "Set up CI/CD pipeline" → Just create the workflow files
- "Create database schema" → Just write the migration

### Underuse
Do mark tasks HUMAN when you literally cannot proceed:
- You need a credential value that doesn't exist yet
- Someone needs to create an external account
- A subjective decision blocks implementation

## Extension Points

- Add team-specific HUMAN triggers
- Implement escalation timelines
- Set up HUMAN item notifications
