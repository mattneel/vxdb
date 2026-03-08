---
name: bug-archaeologist
description: Store, search, and analyze bug history using vxdb. Builds institutional memory of failure patterns so past debugging work is never lost.
---

# Bug Archaeologist

## Triggers

Use this skill when the user says things like:
- "have we seen this before", "similar bugs", "bug archaeology"
- "what caused X last time", "debug history", "why does this keep happening"
- "log this bug", "record this fix"

Also: after a debugging session that took more than 2 rounds of back-and-forth, **proactively offer** to log the bug. Say: "That was a non-trivial fix. Want me to log it in bug history so we can find it if this happens again?"

**Do NOT use this skill for:** active debugging (use debugging tools instead), simple typo fixes, or known issues where the user already knows the solution.

## Setup

Ensure the `bugs` table exists. Call `mcp__vxdb__create_table` with:

- **name:** `bugs`
- **schema:**
  - `title` — `string` — short descriptive name
  - `symptoms` — `text:embed` — natural language description of what was observed (error messages, unexpected behavior, conditions that triggered it)
  - `root_cause` — `text:embed` — technical explanation of why it happened
  - `fix` — `string` — what was actually changed to resolve it
  - `component` — `string` — module, file, or subsystem affected (e.g. "auth", "api/routes", "database")
  - `severity` — `string` — one of: `critical`, `high`, `medium`, `low`
  - `date` — `string` — ISO date when the bug was found (YYYY-MM-DD)
  - `ticket` — `string` — issue/ticket reference if available, empty string otherwise

If the table already exists (create returns an error), that's fine — continue.

## Logging a Bug

When recording a bug after a fix, gather these from the conversation context:

1. **title** — a concise name, e.g. "Race condition in websocket reconnect"
2. **symptoms** — write this in natural language. Include: error messages (exact text when possible), what the user observed, what conditions triggered it, how it manifested. This is the primary search field — make it rich. Example: "Connection drops silently after ~30 seconds of inactivity. No error in browser console. Server logs show 1006 close code. Only happens behind nginx reverse proxy."
3. **root_cause** — the technical "why". Example: "nginx proxy_read_timeout defaults to 60s but the websocket heartbeat interval was set to 90s. Proxy killed idle connections before heartbeat could fire."
4. **fix** — the actual change. Example: "Set proxy_read_timeout to 120s in nginx.conf and reduced heartbeat interval to 30s"
5. **component** — the affected area. Ask the user if unclear.
6. **severity** — assess based on impact. Ask the user if unclear.
7. **date** — use today's date.
8. **ticket** — ask the user, or use empty string.

Insert using `mcp__vxdb__insert` into the `bugs` table.

## Searching for Similar Bugs

When the user reports a new issue or asks "have we seen this before":

### Step 1: Search by symptoms
```
SELECT * FROM bugs WHERE NEAR(symptoms, '<describe what the user is seeing>', 5)
```
Look at `_similarity` scores. Scores above 0.7 are strong matches worth highlighting.

### Step 2: Search by root cause (if the user has a theory)
```
SELECT * FROM bugs WHERE NEAR(root_cause, '<the suspected cause>', 5)
```

### Step 3: Filter by component if known
```
SELECT * FROM bugs WHERE component = '<component>' AND NEAR(symptoms, '<symptoms>', 5)
```

### Step 4: Filter by severity for triage
```
SELECT * FROM bugs WHERE severity = 'critical' AND NEAR(symptoms, '<symptoms>', 5)
```

Present results clearly: title, date, similarity score, and the root cause/fix. Highlight what's similar and what's different from the current situation.

## Pattern Detection

When the user asks about recurring problems or failure patterns:

### Most-affected components
```
SELECT component, COUNT(*) AS cnt FROM bugs GROUP BY component ORDER BY cnt DESC
```

### Recent bugs in a component
```
SELECT title, date, severity, root_cause FROM bugs WHERE component = '<component>' ORDER BY date DESC LIMIT 10
```

### Find recurring root causes
```
SELECT * FROM bugs WHERE NEAR(root_cause, '<category like "timeout" or "race condition" or "null pointer">', 10)
```
Group results and look for themes. Report patterns like: "3 of the last 5 bugs in the `api` component were related to timeout handling."

## Presenting Results

When showing bug history to the user:

- Lead with the **most similar** past bug and its fix — this is what they care about
- Note if the same component has had multiple issues (pattern signal)
- If a past fix is directly applicable, say so explicitly
- If no similar bugs are found, say so — don't force a match
- When showing multiple results, sort by similarity score descending
