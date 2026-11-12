# Semantic Search MCP

Find records by meaning — **the reference extension for queued embedding,
permission-filtered retrieval and a complete index lifecycle.**

An extension to [`albanna-ai-chat`](https://github.com/albannatech/albanna-ai-chat).
Install the core first; this app contributes tools, an agent and prompts to it.

```bash
bench get-app https://github.com/albannatech/semantic-search-mcp.git
bench --site {site} install-app semantic_search_mcp
```

---

## The load-bearing decision

**The index is a recall optimisation, never an authorisation boundary.**

A vector store has no permission engine. Query it and you get the nearest
neighbours — including records the caller may not read. So retrieval is two
stages, and the second is not optional:

1. **Recall** — ask the index for candidates. This stage knows nothing about who
   is asking.
2. **Filter** — ask Frappe what the caller may see, per record, and drop
   everything else.

A record the caller may not read does not appear in the response at all — not
downranked, not truncated, not marked. A response that names it has already
leaked it.

**What the filter does not close:** the *existence* of a near-match leaks
information. A user who cannot see a Customer can still learn that a customer
matching their query exists, and approximately what it says. Nothing available
to this extension closes that channel, and it is documented as accepted risk
rather than pretended away.

---

## What it demonstrates

| Decision | Where |
|---|---|
| **Embedding is queued, never inline** | `after_insert`, `on_update`, `on_trash` all enqueue and return. A hook that called a provider would make a third-party API a dependency of every save on the site. |
| **Permission-filtered retrieval** | `semantic_search` reports three numbers: recalled, permitted, shown. |
| **A complete lifecycle** | Insert, update, trash, reindex, and a drift detector that is a *tool*, not only a test. |
| **Field selection defaults to nothing** | The installer ships `fields_by_doctype` empty. A field nobody chose is not indexed. |
| **Model version is pinned** | Recorded with every vector, so a model change is visible rather than silent. |

### The surface

Six tools, no flow — indexing is queued work whose result is derived data, so
there is no transaction to guard.

| Tool | Tier | Undone by |
|---|---|---|
| `semantic_search` | read | — |
| `list_indexed_doctypes` | read | — |
| `index_drift` | read | — |
| `index_doctype` | write | `unindex_doctype` |
| `reindex_doctype` | write | irreversible — says why |
| `unindex_doctype` | write | irreversible — says why |

---

## A note on the `search_text` parameter

The first draft named it `query`. The core's validator refused the whole
extension: `query` is in `SQL_PARAM_NAMES`, because a parameter by that name is
how a chat tool becomes a raw SQL primitive that bypasses Frappe's permission
engine.

The rule is load-bearing and was not weakened. The parameter was renamed to
`search_text` — which is also the more honest name, since it is text to search
*by*, not a query to *run*.

---

## Configuration

`Semantic Search MCP Settings` (Single), **empty by default**:

| Field | Default | Why |
|---|---|---|
| `vector_store` | empty | No store until configured. A site with none returns nothing and says so. |
| `embedding_model` | empty | Recorded with every vector. |
| `fields_by_doctype` | empty | One line per DocType. A field nobody chose is not indexed. |
| `excluded_fields` | empty | Never embedded, whatever else is configured. |
| `max_candidates` | 200 | Bounded, because the filter runs per candidate. |

---

## Testing

```bash
python -m pytest tests/
```

51 tests. `test_retrieval.py` is the security-critical half: it asserts that an
unpermitted record is dropped *entirely* rather than downranked, that the filter
is per-record rather than per-doctype, that the limit is applied *after*
filtering, and that drift is reported in both directions.

The core is expected at `../albanna-ai-chat`; see `pythonpath` in
`pyproject.toml`.

---

## Requires core

`>=0.3.0,<0.4.0` — the first core version carrying the async primitive. An
earlier core has no way to queue embedding work safely.
