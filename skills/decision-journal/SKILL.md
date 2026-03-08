---
name: decision-journal
description: Log and recall architectural decisions using vxdb. Triggers on "log this decision", "why did we...", "have we decided on...", "what was the rationale for...", "decision journal", "ADR", or when a design discussion reaches a conclusion. NOT for temporary notes, task tracking, or bug reports.
---

# Decision Journal

Persistent architectural decision log backed by vxdb. Decisions survive across sessions and are searchable by semantic similarity.

## Table Setup

On first use, create the table (skip if it already exists — check with `list_tables` first):

```
table: decisions
schema:
  title:        string        # Short name, e.g. "Use SQLite over Postgres"
  context:      text:embed    # What problem were we solving? Full situation description
  decision:     text:embed    # What did we decide? The actual choice made
  alternatives: string        # What else was considered, pipe-separated
  rationale:    string        # Why this over the alternatives — the core reasoning
  domain:       string        # Area: "api", "data", "infra", "auth", "frontend", etc.
  status:       string        # "active", "superseded", "revisited"
  date:         string        # ISO date, e.g. "2026-03-08"
```

Both `context` and `decision` are `text:embed` so NEAR() works on either — search by problem space or by solution.

## Workflow: Logging a Decision

When the user says "log this decision" or a design discussion reaches a clear conclusion:

1. **Extract from conversation** — Do not ask the user to fill out a form. Pull these from the discussion:
   - `title`: One-line summary of what was decided
   - `context`: The problem, constraints, and situation that led here. Be thorough — future-you needs this
   - `decision`: The specific choice made and how it will be implemented
   - `alternatives`: What else was on the table and briefly why each was rejected
   - `rationale`: The core "why" — what tipped the scales
   - `domain`: Categorize by system area
   - `status`: Default "active"
   - `date`: Today's date

2. **Check for related past decisions** before inserting:
   ```
   SELECT * FROM decisions WHERE NEAR(context, '<summary of current problem>', 5)
   ```
   If a closely related decision exists, tell the user. They may want to supersede it rather than create a duplicate.

3. **Insert the decision** using `mcp__vxdb__insert`.

4. **Confirm** with a short summary: title, domain, and whether it relates to any prior decisions.

## Workflow: Recalling Decisions

When the user asks "why did we...", "have we decided on...", "what was the rationale for...":

1. **Search by problem context** (when asking "why did we..." or "what was the rationale for..."):
   ```
   SELECT * FROM decisions WHERE NEAR(context, '<what the user is asking about>', 5)
   ```

2. **Search by decision content** (when asking "have we decided on..." or looking for a specific choice):
   ```
   SELECT * FROM decisions WHERE NEAR(decision, '<what the user is asking about>', 5)
   ```

3. **Filter by domain** when the question is domain-specific:
   ```
   SELECT * FROM decisions WHERE domain = 'api' AND NEAR(context, '<query>', 5)
   ```

4. **Present results** with: title, date, status, the rationale, and alternatives that were rejected. If the decision has been superseded, say so and show the replacement.

## Workflow: Superseding a Decision

When a new decision replaces an old one:

1. **Update the old decision's status** to "superseded" using `mcp__vxdb__update`.
2. **Insert the new decision** with context that references the old one, e.g.:
   `context: "Supersedes: 'Use REST for internal services'. Now that we have gRPC infrastructure, the original constraint no longer applies. ..."`
3. Tell the user both actions were taken.

## Workflow: Analytics and Review

When the user asks for a review, or use these proactively when relevant:

**Decisions by domain:**
```
SELECT * FROM decisions WHERE domain = '<domain>' AND status = 'active' ORDER BY date DESC
```

**Recent decisions (last 30 days):**
```
SELECT title, domain, date FROM decisions WHERE date >= '2026-02-06' ORDER BY date DESC
```

**Superseded decisions (may need cleanup):**
```
SELECT title, domain, date FROM decisions WHERE status = 'superseded'
```

**All active decisions in a domain:**
```
SELECT * FROM decisions WHERE domain = 'api' AND status = 'active'
```

Use these to surface patterns: "You have 8 active infra decisions, 3 of which are from 6+ months ago — worth revisiting?"

## Guidelines

- **Capture context generously.** The decision itself is obvious in hindsight. The context — why it was hard, what constraints applied, what tradeoffs were made — is what gets lost.
- **Don't log trivial choices.** If it wouldn't matter in 3 months, it's not a decision, it's a preference.
- **Actively surface prior decisions.** When a design discussion starts, search for related decisions before the user asks. "Before we go further — 2 months ago you decided X for similar reasons."
- **Keep status current.** A journal full of superseded-but-not-marked decisions is worse than no journal.
- **One decision per entry.** If a discussion yields 3 decisions, log 3 entries.
