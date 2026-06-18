<!--
  /research <topic> — runs five research steps before any planning or
  implementation: web search, fetch docs, grep the codebase, find companion
  packages, find reference implementations. Outputs a structured findings
  summary. The Project Context section pins the stack so searches target the
  right ecosystem without restating it each run.

  Planning from training data alone risks stale packages, missed
  ecosystem-standard patterns, or duplicating what the codebase already does;
  this forces a pass grounded in current docs and the project's actual state.
-->

# /research

Research the topic before any planning or implementation begins.

## Project Context

<!-- Generated stack snapshot — keep in sync with the project's deps -->
- **Language / runtime**: Python 3.11
- **Package manager**: uv
- **Key frameworks**: FastAPI 0.115, SQLAlchemy 2.0, Pydantic 2.x, uvicorn
- **Formatter**: ruff

## Topic

<topic>#$ARGUMENTS</topic>

If the topic is empty, ask: "What are you researching? Give me a brief
description and I'll run the full research pass."

Do not proceed until a topic is provided.

## Step 1 — Web search

Search for current best-practice packages, tools, and approaches for this
topic in the ecosystem defined in the Project Context above.

Do not rely on training data — ecosystems move fast. Prioritise results from
the past 12 months.

## Step 2 — Fetch documentation

Fetch the README or official docs for the top 3–5 most relevant packages or
tools surfaced in Step 1. Read enough to understand:

- Full feature set and configuration options
- Integration patterns for this project's stack
- Known limitations or gotchas

## Step 3 — Search the codebase

Grep and glob the project for any existing usage of related packages, similar
patterns, or prior attempts at solving the same problem.

If something relevant already exists, note it — the recommendation should
build on or replace it deliberately, not ignore it.

## Step 4 — Look for companion packages

Check for plugins, auto-instrumentations, middleware, adapters, or companion
packages that extend the core tool. These are often where the biggest quality
or developer-experience gains hide and are easy to miss in a naive
implementation.

## Step 5 — Find reference implementations

Search for well-regarded open-source projects in a similar stack that have
solved the same problem. A real implementation is often more instructive than
documentation alone.

## Output

Summarise findings as:

- **Available options** — what exists, maintenance status, community adoption
- **Recommended approach** — what fits this project's stack and why
- **What a naive implementation would miss** — companion packages,
  configuration patterns, dev tooling, conditional loading, etc.
- **Open questions** — tradeoffs or decisions that need input before proceeding
