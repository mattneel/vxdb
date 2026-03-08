---
name: self-evaluator
description: "Log task outcomes with approach metadata, then query past performance to improve future work. Builds few-shot retrieval from lived experience. Triggers: after completing any non-trivial task (proactively offer to log), 'how did I do last time with X', 'what works for tasks like this', 'self-evaluate', 'what am I good at', 'what do I struggle with'. NOT for: trivial tasks, single-line edits, tasks where outcome is obvious."
---

# Self-Evaluator

Performance memory backed by vxdb. Tracks what you tried, whether it worked, and why — so future tasks benefit from past experience instead of starting from zero.

## Why This Matters

Without evaluation logging, you repeat the same mistakes and rediscover the same strategies. This skill closes the loop: do the work, record what happened, retrieve it next time. The critical insight is logging the **approach**, not just the outcome. "I used AST traversal" vs "I used regex" — that's what you want to retrieve when facing a similar task.

## Table Setup

On first use, check if the `evaluations` table exists. If not, create it:

```
mcp__vxdb__list_tables -> check for "evaluations"

mcp__vxdb__create_table:
  name: evaluations
  schema:
    task_description: "text:embed"
    approach:         "text:embed"
    outcome:          "string"     # success | partial | failure | revised
    feedback:         "string"     # user feedback or self-assessment notes
    task_type:        "string"     # code_change | debugging | research | design | refactor | review
    confidence:       "string"     # high | medium | low
    date:             "string"     # YYYY-MM-DD
```

Both `task_description` and `approach` are `text:embed` so NEAR() works on either — search by what was asked or by how it was solved.

## Workflow 1: Log After Completing a Task

After finishing any non-trivial task, proactively offer: "Want me to log this for future reference?"

1. **Extract from the conversation** — don't ask the user to fill out fields. Pull these yourself:
   - `task_description`: What was asked. Be specific enough that NEAR() will match similar future tasks. Bad: "fix the bug". Good: "Fix race condition in websocket reconnection logic causing dropped messages on flaky networks."
   - `approach`: How you tackled it. Include the strategy, key tools, and any pivots. "Started with grep for the error message, found the retry loop lacked backoff, added exponential backoff with jitter, tested with simulated packet loss."
   - `outcome`: Read the user's reaction. Accepted without changes = `success`. Accepted with corrections = `partial`. Rejected or started over = `failure`. Major rework of your approach = `revised`.
   - `feedback`: User's explicit feedback, or your own assessment if none given. "User said the fix was clean but asked to also add a config option for max retries."
   - `task_type`: Categorize — `code_change`, `debugging`, `research`, `design`, `refactor`, or `review`.
   - `confidence`: Your honest self-assessment before seeing the outcome. Were you sure this would work?
   - `date`: Today's date.

2. **Deduplicate** before inserting:
   ```
   SELECT * FROM evaluations WHERE NEAR(task_description, '<new task>', 3)
   ```
   If a result has `_similarity > 0.90`, you may be logging the same task twice. Ask the user or skip.

3. **Insert** using `mcp__vxdb__insert`.

## Workflow 2: Query Before Starting a Similar Task

When starting non-trivial work, search for past experience:

1. **Find similar past tasks:**
   ```
   SELECT * FROM evaluations WHERE NEAR(task_description, '<current task description>', 5)
   ```

2. **Filter for what worked:**
   ```
   SELECT * FROM evaluations WHERE NEAR(task_description, '<current task>', 5) AND outcome = 'success'
   ```

3. **Check what failed** too — knowing what didn't work is equally valuable:
   ```
   SELECT * FROM evaluations WHERE NEAR(task_description, '<current task>', 5) AND outcome = 'failure'
   ```

4. **Adapt strategy.** Tell the user: "I've done something similar before. Approach X worked, approach Y didn't. I'll start with X this time." Then do it.

## Workflow 3: Self-Reflection

When the user asks "what do I struggle with?" or "what am I good at?", or periodically on your own:

**Outcome distribution by task type:**
```
SELECT task_type, outcome, COUNT(*) as count FROM evaluations GROUP BY task_type, outcome
```

**Failure patterns — what approaches keep failing:**
```
SELECT * FROM evaluations WHERE outcome = 'failure' ORDER BY date DESC LIMIT 10
```

**Success patterns — what approaches keep working:**
```
SELECT * FROM evaluations WHERE outcome = 'success' AND NEAR(approach, '<current domain>', 5)
```

Surface patterns plainly: "Debugging tasks succeed 80% of the time. Refactoring tasks have a 40% failure rate — usually because scope creeps."

## Workflow 4: Calibration Check

Compare self-assessed confidence against actual outcomes:

**High confidence but failed:**
```
SELECT * FROM evaluations WHERE confidence = 'high' AND outcome IN ('failure', 'revised')
```

**Low confidence but succeeded:**
```
SELECT * FROM evaluations WHERE confidence = 'low' AND outcome = 'success'
```

If high-confidence tasks fail often, flag it: "I tend to be overconfident on refactoring tasks — I'll be more careful and check assumptions earlier." Adjust future confidence ratings accordingly.

## Guidelines

- **Log the approach, not just the outcome.** "Used DuckDB window functions" is retrievable. "It worked" is not.
- **Be honest about outcomes.** Inflated self-assessment defeats the purpose. If the user had to correct you, that's `partial`, not `success`.
- **Don't log trivial tasks.** A one-line typo fix doesn't need an evaluation entry. Reserve this for tasks where strategy mattered.
- **Proactively retrieve, don't wait to be asked.** When you see a task similar to something logged, surface it before diving in.
- **Update, don't just append.** If you revisit a task and get a better outcome, update the existing entry's outcome and feedback rather than creating a duplicate.
- **Confidence is a prediction, outcome is ground truth.** Track both. The gap between them is where you learn.
