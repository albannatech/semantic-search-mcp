# Changelog

All notable changes to Semantic Search MCP are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the app uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-11-12

### Added

**The reference extension for queued embedding and permission-filtered
retrieval.**

Six tools, one agent, two doctypes, no flow.

- `tools/manifest.py` — the contract. Imports no Frappe, so the core can
  validate it on a machine with none of this app's dependencies installed.
  `REQUIRES_CORE = ">=0.3.0,<0.4.0"`, the first core version carrying the async
  primitive.
- `tools/retrieval.py` — the security-critical half, in Frappe-free code.
  Two-stage retrieval: recall from the index, then filter every candidate
  through Frappe. `SearchReport` names all three numbers — recalled, permitted,
  shown — because a search that reports only `shown` hides the fact that
  permissions bounded it. `drift_report` compares the index against the source
  in both directions, because a missing record returns too little and an
  orphaned entry returns something that no longer exists.
- `tools/store.py` — the vector store port, and an in-memory store documented
  as unsuitable for production rather than quietly assumed to be fine.
- `tools/indexing.py` — the Frappe handlers, and the jobs the worker runs.
  `after_insert`, `on_update` and `on_trash` all enqueue and return.
- `agent.py` — persona, four boundaries, three prompts, and a deterministic
  `system_prompt()`.
- `hooks.py` — `frappe_mcp_tools`, `frappe_mcp_agent`, and an empty
  `doc_events`: nothing is indexed until an operator adds a DocType.
- Doctypes: `Indexed DocType`, `Semantic Search MCP Settings`.
- `install.py` — creates the role and the Single settings row, idempotently.
  The settings ship **empty**: no store, no model, no indexed doctypes, no
  configured fields.
- `tests/test_manifest.py`, `tests/test_retrieval.py` — 51 tests that import
  the **real core** and run this manifest through the core's own validator.

### The decisions it demonstrates

- **The index is a recall optimisation, never an authorisation boundary.**
  Filtering happens after retrieval, per record, through Frappe. The residual
  inference channel — that a near-match *exists* — is documented as accepted
  risk rather than pretended away.
- **Embedding is queued, never inline.** A hook that called a provider would
  make a third-party API a dependency of every save on the site: a slow
  provider makes every write slow, and a down provider makes every write
  *fail*, because a hook that raises rolls back the transaction.
- **The lifecycle is complete.** Insert, update, trash, reindex, drift. The
  deletion hook is the half that is easiest to forget and most visible when it
  is: without it the index keeps entries whose records are gone, and a search
  returns them, and the reader follows a link to nothing.
- **Field selection defaults to nothing.** An extension that installed itself
  ready to embed every field of every doctype would be an extension that made a
  decision nobody authorised.

### Changed

- The search parameter is `search_text`, not `query`. The core's validator
  refused the first draft outright: `query` is in `SQL_PARAM_NAMES`, because a
  parameter by that name is how a chat tool becomes a raw SQL primitive that
  bypasses Frappe's permission engine. The rule is load-bearing and was not
  weakened — the parameter was renamed, which is also the more honest name,
  since it is text to search *by* rather than a query to *run*.

