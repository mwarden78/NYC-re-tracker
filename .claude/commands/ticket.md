# /ticket - Ticket operations

Manage tickets in Linear or Shortcut.

## Usage

```
/ticket list
/ticket list --status "In Progress"
/ticket get PROJ-123
/ticket create "Add user authentication" --description "Add OAuth2 login flow"
/ticket update PROJ-123 --status "Done"
```

## Subcommands

### list
List tickets from the configured tracker.

```
/ticket list                    # All open tickets
/ticket list --status "Todo"    # Filter by status
/ticket list --label "Bug"      # Filter by label
```

### get
Get details for a specific ticket.

```
/ticket get PROJ-123
```

### create
Create a new ticket. **A description is REQUIRED** — never create a ticket without one.

```
/ticket create "Title" --description "Detailed description" --label Feature --label Backend
```

### update
Update an existing ticket.

```
/ticket update PROJ-123 --status "In Progress"
/ticket update PROJ-123 --add-label "High Risk"
```

### link
Link two tickets with a blocking relationship.

```
/ticket link PROJ-101 --blocks PROJ-102
```

## Instructions

When the user invokes `/ticket <subcommand>`:

1. Map to the appropriate `bin/ticket` command
2. Run the command and capture output
3. Format the output nicely for the user
4. For `create`, confirm the ticket ID created
5. For errors, suggest fixes (e.g., "Run `bin/vibe setup` to configure tracker")
