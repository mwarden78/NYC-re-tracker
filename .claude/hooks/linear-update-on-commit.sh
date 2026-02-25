#!/bin/bash
# Linear status update hook - runs after git commits
# Updates ticket status to "Done" when commit message contains ticket ID
#
# Configuration:
#   - Set LINEAR_API_KEY in .env.local
#   - Optionally set TICKET_PATTERN in .env.local (default: [A-Z]+-[0-9]+)

set -e

# Source .env.local if LINEAR_API_KEY not already set
if [ -z "$LINEAR_API_KEY" ] && [ -f ".env.local" ]; then
  export $(grep -E '^LINEAR_API_KEY=' .env.local 2>/dev/null | xargs) || true
fi

# Check for API key - exit silently if not configured
if [ -z "$LINEAR_API_KEY" ]; then
  exit 0
fi

# Get ticket pattern from env or use default
TICKET_PATTERN="${TICKET_PATTERN:-[A-Z]+-[0-9]+}"

# Get the last commit message
COMMIT_MSG=$(git log -1 --pretty=%B 2>/dev/null || echo "")

if [ -z "$COMMIT_MSG" ]; then
  exit 0
fi

# Extract ticket ID using the pattern
TICKET_ID=$(echo "$COMMIT_MSG" | grep -oE "$TICKET_PATTERN" | head -1)

if [ -z "$TICKET_ID" ]; then
  exit 0
fi

echo "Updating Linear ticket $TICKET_ID to Done..."

# Get the issue details and workflow states
ISSUE_RESPONSE=$(curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: $LINEAR_API_KEY" \
  --data "{\"query\": \"query { issue(id: \\\"$TICKET_ID\\\") { id team { states { nodes { id name type } } } } }\"}" \
  https://api.linear.app/graphql 2>/dev/null)

# Extract issue ID
ISSUE_ID=$(echo "$ISSUE_RESPONSE" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

if [ -z "$ISSUE_ID" ] || [ "$ISSUE_ID" = "null" ]; then
  echo "Could not find Linear issue $TICKET_ID"
  exit 0
fi

# Find the "Done" state ID (type: completed)
DONE_STATE_ID=$(echo "$ISSUE_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    states = data.get('data', {}).get('issue', {}).get('team', {}).get('states', {}).get('nodes', [])
    for state in states:
        if state.get('type') == 'completed' and state.get('name', '').lower() == 'done':
            print(state['id'])
            break
    else:
        # Fallback to any completed state
        for state in states:
            if state.get('type') == 'completed':
                print(state['id'])
                break
except:
    pass
" 2>/dev/null)

if [ -z "$DONE_STATE_ID" ]; then
  echo "Could not find 'Done' state for team"
  exit 0
fi

# Update the issue status
UPDATE_RESPONSE=$(curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: $LINEAR_API_KEY" \
  --data "{\"query\": \"mutation { issueUpdate(id: \\\"$ISSUE_ID\\\", input: { stateId: \\\"$DONE_STATE_ID\\\" }) { success } }\"}" \
  https://api.linear.app/graphql 2>/dev/null)

if echo "$UPDATE_RESPONSE" | grep -q '"success":true'; then
  echo "Linear ticket $TICKET_ID marked as Done"
else
  echo "Failed to update Linear ticket"
fi
