---
name: issue
description: Draft and create a GitHub issue with consistent title, body, and labels — always shows draft before creating
user-invocable: true
disable-model-invocation: true
---

# /issue

Draft a GitHub issue following this project's conventions, then create it
after user approval. Never create an issue without showing the full draft first.

## Invocation

```
/issue [brief description of what you want to track]
```

If no argument is provided, ask the user what the issue is about before proceeding.

---

## Step 1 — Classify the issue type

Pick the template that fits:

- **Problem/gap** — something is missing, wrong, or needs improvement. Use when the right solution isn't obvious. Forces options and tradeoffs before committing to an approach. Default when unsure.
- **Feature spec** — a planned feature with scope and deliverables already decided. Use only when the approach is clear.
- **Bug** — observable incorrect behavior with reproduction steps.

---

## Step 2 — Draft the title

Format: `type(scope): imperative description`

**Types** — must match the label applied:

| Type       | Use for                                      |
|------------|----------------------------------------------|
| `feat`     | New capability or behavior                   |
| `fix`      | Bug fix                                      |
| `chore`    | Infra, deps, tooling, maintenance            |
| `ci`       | CI/CD pipeline and workflow changes          |
| `docs`     | Documentation only                           |
| `refactor` | Code restructuring, no behavior change       |

**Scopes** — optional but preferred:

| Scope           | Owns                                          |
|-----------------|-----------------------------------------------|
| `auth`          | Authentication, JWT, tokens, sessions         |
| `jobs`          | Job creation, entities, redaction flow        |
| `db`            | Database, models, migrations                  |
| `infra`         | Terraform, AWS infrastructure                 |
| `api`           | General routing / API surface                 |
| `security`      | Security-specific concerns                    |
| `observability` | Logging, monitoring, alerting                 |
| `deps`          | Dependency updates                            |

**Description rules:**
- Imperative mood: "add", "fix", "migrate", "remove" — not "added" or "adds"
- Lowercase after the colon
- No trailing period
- Specific enough to understand without reading the body

**Examples:**
- `feat(auth): add OAuth2 login via GitHub`
- `fix(jobs): handle empty Comprehend response on short text`
- `chore(infra): add default tags to all Terraform resources`
- `ci: add deployment health check to deploy workflow`
- `refactor(jobs): extract entity filtering into service layer`

---

## Step 3 — Draft the body

### Problem/gap template (default)

```
## Problem
[What is wrong, missing, or suboptimal. One paragraph, factual, no proposed solution.]

## Why it matters
[Consequence of not addressing it — who is affected, what breaks, what risk exists.]

## Priority
[short-term / mid-term / long-term] — [one-sentence rationale]

## Options

### A — [Option name]
[Description of approach]

**Tradeoffs:** [What this gains and what it costs or limits]

### B — [Option name]
[Description of approach]

**Tradeoffs:** [What this gains and what it costs or limits]

## Preference
[State a preference and reasoning, or explicitly state "No strong preference" and why.]
```

### Feature spec template

Use only when scope and deliverables are already decided.

```
## Goal
[One sentence: what this feature does and for whom.]

## Priority
[short-term / mid-term / long-term] — [one-sentence rationale]

## Deliverables
- [Specific, completable item]
- [Specific, completable item]

## Tests
- [What must pass or be verified]
- [What must pass or be verified]
```

### Bug template

```
## Problem
[Observable incorrect behavior — what is happening that shouldn't be.]

## Steps to reproduce
1. [Step]
2. [Step]

## Expected
[What should happen.]

## Actual
[What happens instead.]

## Environment
[Relevant versions, config, or context needed to reproduce.]
```

---

## Step 4 — Select labels

Apply exactly **one type label** and **one priority label**. Add `blocked`
only if this issue cannot start until another specific issue is resolved —
and if so, note it explicitly at the top of the body:
`**Blocked by #N** — [reason]`

| Condition                              | Label      |
|----------------------------------------|------------|
| New capability                         | `feat`     |
| Bug fix                                | `fix`      |
| Infra / deps / tooling / maintenance   | `chore`    |
| CI/CD pipeline                         | `ci`       |
| Documentation only                     | `docs`     |
| Code restructuring, no behavior change | `refactor` |
| Must ship before first real user       | `p:high`   |
| Must ship before scale or team growth  | `p:mid`    |
| Nice to have, no immediate urgency     | `p:low`    |
| Waiting on another issue               | `blocked`  |

---

## Step 5 — Show draft and wait for approval

Present the full draft:

```
**Title:** type(scope): description
**Labels:** label, p:priority

**Body:**
[full body content]
```

Ask if the user wants to adjust anything. Do not create the issue until explicitly confirmed.

---

## Step 6 — Create the issue

```bash
gh issue create \
  --title "type(scope): description" \
  --label "label,p:priority" \
  --body "$(cat <<'EOF'
[body]
EOF
)" \
  --repo MikepdXRider/redactcat
```

Return the issue URL when done.
