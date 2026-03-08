---
name: research-synthesizer
description: Deep research workflow that gathers findings from multiple sources, stores them in vxdb with metadata, uses vector similarity to find connections, and synthesizes a structured summary.
---

# Research Synthesizer

## When to use

Triggers: "research X", "survey X", "deep dive on X", "what do we know about X", "investigate X"

NOT for: quick lookups, single-source reads, questions with obvious answers. If the answer is one search away, just answer it directly.

## Workflow

### Phase 1: Setup

Create a vxdb table for this research session. Use a slugified topic name with `_research` suffix (e.g., `raft_consensus_research`).

Schema:

```
topic: string        — sub-topic or category within the research
finding: text:embed  — the actual finding, written as a clear statement
source: string       — where this came from (URL, file path, paper title, etc.)
confidence: string   — "high", "medium", or "low"
tags: string         — comma-separated relevant tags
timestamp: string    — ISO 8601 timestamp of when the finding was recorded
```

Example:

```
mcp__vxdb__create_table(
  name: "raft_consensus_research",
  schema: {
    "topic": "string",
    "finding": "text:embed",
    "source": "string",
    "confidence": "string",
    "tags": "string",
    "timestamp": "string"
  }
)
```

### Phase 2: Broad sweep

Cast a wide net. Use WebSearch, WebFetch, file reads, and code search to gather initial findings across the topic. For each meaningful finding:

1. Write the finding as a clear, self-contained statement (not a copy-paste dump).
2. Assign a confidence level based on source quality and corroboration.
3. Tag it with relevant sub-topics.
4. Insert it immediately — don't batch at the end.

```
mcp__vxdb__insert(
  table: "raft_consensus_research",
  rows: [{
    "topic": "leader election",
    "finding": "Raft uses randomized election timeouts (150-300ms) to avoid split votes. Each server restarts its timeout independently, so one server will usually time out and win election before others.",
    "source": "https://raft.github.io/raft.pdf",
    "confidence": "high",
    "tags": "leader-election,timeouts,split-vote",
    "timestamp": "2026-03-08T14:00:00Z"
  }]
)
```

Insert findings in small batches (1-5 rows) as you go. This keeps the vector index fresh for gap analysis.

### Phase 3: Gap analysis with NEAR()

After collecting 8-15 initial findings, pause and probe for gaps. Use NEAR() queries to see what the research covers and what it doesn't.

**Check coverage of a sub-topic:**
```
SELECT topic, finding, confidence FROM {table}
  WHERE NEAR(finding, 'failure recovery mechanisms', 5)
```

**Find weak spots (low-confidence findings on important topics):**
```
SELECT * FROM {table}
  WHERE confidence = 'low' AND NEAR(finding, '{core topic}', 10)
```

**Look for unexpected connections:**
Pick a finding you didn't expect and run NEAR() against it. Surprising neighbors reveal cross-cutting themes worth investigating.

Review the NEAR() results and identify:
- Sub-topics with zero or one finding (gaps)
- Clusters of low-confidence findings (needs verification)
- Surprising connections between sub-topics (worth exploring)

### Phase 4: Targeted deep dives

For each gap or weak area identified in Phase 3:

1. Do focused research on that specific sub-topic.
2. Insert new findings with the same process as Phase 2.
3. If a finding contradicts an earlier one, insert the new finding AND update the old one's confidence to "low".

```
mcp__vxdb__update(
  table: "raft_consensus_research",
  id: "{id_of_contradicted_finding}",
  set: { "confidence": "low" }
)
```

Repeat Phases 3-4 until coverage is satisfactory or diminishing returns set in. Usually 2-3 cycles is enough.

### Phase 5: Synthesis

Query thematically, not chronologically. Pull findings by topic clusters using NEAR().

**Step 1 — Identify themes:**
```
SELECT DISTINCT topic FROM {table}
```

**Step 2 — For each theme, pull the best findings:**
```
SELECT finding, source, confidence FROM {table}
  WHERE NEAR(finding, '{theme description}', 10)
  ORDER BY _similarity DESC
```

**Step 3 — Build the summary.** Structure it as:

```
## Research Summary: {Topic}

### Key Findings
- [High confidence findings, synthesized into clear statements]

### Sub-topic: {Name}
- Finding (Source) [Confidence]
- Finding (Source) [Confidence]
...repeat for each sub-topic...

### Connections & Patterns
- Cross-cutting themes discovered via NEAR() similarity

### Gaps & Open Questions
- What remains unclear or under-researched

### Sources
- Numbered list of all unique sources cited
```

Prioritize high-confidence findings. Flag contradictions explicitly. Cite every claim.

### Phase 6: Cleanup (optional)

Ask the user if they want to keep the research table for future reference or drop it.

```
mcp__vxdb__drop_table(name: "raft_consensus_research")
```

## Guidelines

- **Write findings as statements, not notes.** "Raft guarantees at most one leader per term" not "raft leader stuff - one per term??"
- **Be honest about confidence.** High = peer-reviewed or primary source. Medium = reputable secondary source. Low = single unverified source or inference.
- **Insert early, insert often.** The value of NEAR() scales with the number of findings in the table.
- **Don't over-tag.** 2-4 tags per finding. Tags are for filtering, NEAR() handles semantic similarity.
- **Contradictions are findings.** When sources disagree, record both sides and note the conflict.
- **Stop when returns diminish.** If Phase 3 shows good coverage across sub-topics with mostly high/medium confidence, move to synthesis.
