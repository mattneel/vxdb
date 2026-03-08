---
name: codebase-cartographer
description: Index and semantically search a codebase using vxdb. Builds a navigable knowledge graph of modules, components, patterns, and their relationships.
---

# Codebase Cartographer

## Triggers

Activate when the user says: "index this codebase", "map this project", "how does X work in this codebase", "onboard me to this project", "cartograph", or asks architectural questions about the current codebase.

Do NOT activate for: reading a single specific file, simple grep searches, or projects with fewer than 10 files.

## Schema

Create three vxdb tables. Check `mcp__vxdb__list_tables` first — skip creation if tables already exist.

### Table: `modules`

```
name: string        — module/package/directory name
description: text:embed  — what this module does, its responsibilities
path: string        — relative path from project root
language: string    — primary language (e.g. "python", "typescript", "rust")
```

### Table: `components`

```
name: string        — function, class, endpoint, or config name
purpose: text:embed — what this component does, why it exists
module: string      — parent module name (foreign key to modules.name)
path: string        — file path relative to project root
kind: string        — one of: function, class, endpoint, config, type, constant
```

### Table: `patterns`

```
name: string            — pattern name (e.g. "error handling", "dependency injection")
description: text:embed — how the pattern works in this codebase
examples: string        — comma-separated file paths demonstrating the pattern
category: string        — one of: architecture, convention, error-handling, testing, data-flow
```

## Indexing Workflow

### Step 1: Scan structure

Use the Glob tool or an Explore subagent to scan the directory structure. Identify top-level modules by directory layout — `src/`, `lib/`, `pkg/`, `app/`, `internal/`, etc. Note the primary language from file extensions.

### Step 2: Index modules (top-down)

For each top-level module directory:

1. Read key files: README, index/mod/init files, main entry points.
2. Write a 1-3 sentence description capturing the module's responsibility.
3. Insert into `modules` table via `mcp__vxdb__insert`.

Batch inserts — send multiple rows per `mcp__vxdb__insert` call.

### Step 3: Index components (per module)

Within each module, identify the important components:

- **Functions/methods**: exported/public functions, handlers, key internal functions.
- **Classes/structs**: data models, services, controllers.
- **Endpoints**: API routes, CLI commands, event handlers.
- **Config**: configuration files, environment schemas, constants.

For each component, write a concise `purpose` string that describes *what it does and why*. This is the field that gets embedded, so make it semantically rich. Example: "Validates JWT tokens from the Authorization header and extracts user claims for downstream middleware" — not just "validates tokens".

Insert via `mcp__vxdb__insert` in batches of 10-20 rows.

### Step 4: Identify patterns

After indexing modules and components, look for cross-cutting patterns:

- Error handling conventions (how errors propagate, custom error types)
- Authentication/authorization flow
- Data access patterns (repository pattern, direct queries, ORM usage)
- Testing conventions (unit test structure, fixtures, mocks)
- Dependency injection or service initialization
- Logging and observability patterns

Insert each pattern into the `patterns` table with example file paths.

## Query Workflow

### Semantic search (most common)

When the user asks "how does X work?" or "what handles Y?", use `NEAR()` on the embedded columns:

```sql
SELECT * FROM components WHERE NEAR(purpose, 'authentication and user login', 10)
```

Filter by module or kind to narrow results:

```sql
SELECT * FROM components WHERE module = 'api' AND NEAR(purpose, 'request validation', 5)
SELECT * FROM components WHERE kind = 'endpoint' AND NEAR(purpose, 'user management', 10)
```

Search modules for high-level questions:

```sql
SELECT * FROM modules WHERE NEAR(description, 'handles payment processing', 5)
```

Search patterns for convention questions:

```sql
SELECT * FROM patterns WHERE NEAR(description, 'how errors are handled', 5)
```

### Cross-module queries

To understand relationships, query components across modules:

```sql
SELECT * FROM components WHERE module = 'auth' AND kind = 'function'
SELECT * FROM components WHERE NEAR(purpose, 'database connection', 10)
```

### Following the trail

After finding relevant components via semantic search:

1. Read the actual source files at the returned `path` values.
2. Trace dependencies — if component A calls component B, search for B.
3. Present findings as a coherent narrative, not a list of files.

## Updating the Index

When code changes during a session:

1. Use `mcp__vxdb__delete` to remove stale entries: `where: "path = 'old/path.ts'"` or `where: "module = 'refactored_module'"`.
2. Re-index the changed files with `mcp__vxdb__insert`.
3. If a module's responsibility changed, use `mcp__vxdb__update` with `set: { description: "new description" }` and `where: "name = 'module_name'"`.

For full re-index, `mcp__vxdb__drop_table` all three tables and start over.

## Output Guidelines

- When onboarding: present a top-down summary starting with modules, then key components per module, then notable patterns. Keep it scannable.
- When answering questions: lead with the direct answer, then show the relevant components and files. Read the actual code to confirm before answering.
- When indexing: report progress as you go ("Indexed 5 modules, 47 components, 3 patterns"). Show the final counts when done.
- Always provide file paths so the user can navigate directly to relevant code.
