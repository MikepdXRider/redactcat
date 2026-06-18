<!--
  /pr-review <PR#> — checks out the PR branch, runs three review agents in
  parallel (1: structure/maintainability, 2: correctness/behavior, 3: this
  project's conventions), then restores the original branch. Writes a
  consolidated Blocker/Suggestion/Nit report to claude-artifacts/reviews/;
  never posts to GitHub.
-->

# /pr-review

Run a multi-agent code review for PR #$ARGUMENTS.

If `$ARGUMENTS` is empty, ask: "Which PR number should I review?"

## Phase 1 — Setup

1. **Refuse to run against a dirty tree.** Run `git status --porcelain`; if it
   returns anything, stop and tell the user to commit or stash first.
2. **Record the current ref** so it can be restored in Phase 4:
   `git symbolic-ref --quiet --short HEAD || git rev-parse HEAD`
3. Fetch PR metadata:
   `gh pr view $ARGUMENTS --json title,body,baseRefName,headRefName,files`
4. **Check out the PR head:**
   `gh pr checkout $ARGUMENTS`
5. Capture the diff:
   `gh pr diff $ARGUMENTS`
6. **Verify the branch is green.** Run `uv run ruff check .` and `uv run pytest`.
   If either fails, record the failure under Blockers and continue the review —
   do not abort, but the verdict must be NO MERGE.

## Phase 2 — Parallel Review

Launch all three agents simultaneously.

Every agent logs: (a) **assumptions** made, (b) **ambiguities** navigated,
(c) anything it **could not verify**. These feed Phase 3.

---

**Agent 1 — Structural Review**

Read the full current files, not just the diff.

- Are new abstractions justified, or premature?
- Is the data flow clear and traceable end-to-end?
- **Stale names** — do identifiers still describe what they represent?
- **Dead/redundant code** — unused parameters, constants that collapse to one value?
- **Duplicated patterns** — 3+ identical blocks that should become a shared helper?
- **Non-critical side effects** — are logging/analytics calls wrapped so a
  third-party outage can't break a user-facing operation?
- **Pre-existing issues** — only flag when the PR makes it worse or load-bearing.

---

**Agent 2 — Behavioral Review**

Read the full current files, not just the diff.

- Does the implementation match the PR description? Treat the code as truth.
- Subtle bugs in conditional or branching logic?
- Missing edge cases or error-handling gaps?
- **Data exposure** — PII or sensitive fields reaching a response DTO that
  shouldn't carry them.
- **Access control** — protected endpoints enforce ownership; correct 401 / 404
  precedence. This project raises `404` (not `403`) for resources that exist but
  belong to another user — do not flag this as incorrect.
- **Performance** — no N+1 queries; queries scale with data size.
- **Persistence** — writes return fully-populated state; multi-row writes are
  atomic. Assess by code inspection of the transaction boundary. After a write +
  commit, check the response schema: if it includes relationships, re-fetch with
  `joinedload` (single record) or `selectinload` (list) is required — `db.refresh()`
  only reloads scalar columns and will trigger lazy queries per relationship during
  Pydantic serialization. `db.refresh()` is acceptable when the response schema is
  flat (no relationships). Flag write endpoints that use `db.refresh()` and return
  relationship fields.
- **Schema changes** — detect whether Alembic or equivalent exists.
  If it does, schema-changing PRs need a reversible migration.
  If it does not, record the change under Assumptions & Ambiguities.
- **Test coverage** — invoke `/api-testing` for the full coverage matrix.
  Every endpoint changed by this PR must be covered for auth enforcement,
  input validation, cross-user isolation (if applicable), exact response shape,
  and any DB-side effects not surfaced by HTTP.

---

**Agent 3 — Conventions & Standards**

Derive this codebase's standards from three sources:

1. **The code itself** — sample `app/modules/*`, `app/main.py`, `app/database.py`,
   `app/config.py`. Extract patterns: type hints; Pydantic DTOs with
   `ConfigDict(from_attributes=True)` and naming conventions below;
   `HTTPException` for errors; naive UTC datetimes; `get_db` session lifecycle;
   eager-loading strategy. A weakness in established patterns is still a finding.
2. **Project spec** — `README.md` or `CONTRIBUTING` if present.
3. **Curated preferences** — `CLAUDE.md`. Apply as overlay; flag conflicts
   with (1) or (2) explicitly — the doc may be stale.

**Schema naming conventions to enforce:**
- `XRead` — response DTOs
- `XCreate` — request body schemas
- `XUpdate` — partial update schemas (all fields optional)
- `XLogin` — auth input schemas; intentionally omit validation constraints
  (e.g. no `min_length` on password) so wrong credentials return `401`, never `422`.
  Do not flag missing `min_length` on login schemas as a violation.
- Shared validation values (e.g. password length) must be defined as a
  module-level constant (e.g. `PASSWORD_MIN_LENGTH = 8`) and referenced by name —
  never repeated inline across multiple schemas.

Produce a pass/fail table of changed files against standards from (1) and (2).

---

## Phase 3 — Synthesis

1. **Deduplicate** findings across agents.
2. **Detect contradictions** — record both positions; apply precedence
   (correctness > consistency, spec > curated overlay) or surface the conflict.
3. **Categorize severity:**
   - **Blocker** — incorrect behavior, data loss, security regression, broken
     API contract, or PR doesn't do what it claims.
   - **Suggestion** — correct but improvable.
   - **Nit** — naming, formatting, no behavioral consequence.
4. **Verdict:** `NO MERGE` iff ≥1 Blocker.
5. Include Agent 3's pass/fail table verbatim — never omit.
6. Include a **Conflicts** section (doc vs. code/spec). "None found." if none.
7. Include a **Contradictions** section (agent vs. agent). "None found." if none.
8. Include an **Assumptions & Ambiguities** section from all three agents.
9. Check if `claude-artifacts/reviews/pr-review-$ARGUMENTS.md` exists;
   if so, increment version suffix (`-v2`, `-v3`, …).
10. Write the report to that path.

**Output format:**

```
# PR #N Review: <title>

## Verdict: MERGE / NO MERGE

## Blockers
## Suggestions
## Nits
## Contradictions
## Conflicts
## Assumptions & Ambiguities
## Conventions & Standards
<Agent 3 pass/fail table — verbatim, never omit>
```

## Phase 4 — Cleanup

Restore the branch recorded in Phase 1: `git checkout <recorded-ref>`

Do this even if the review aborts early.

**Never post review comments to GitHub. All output is local only.**
