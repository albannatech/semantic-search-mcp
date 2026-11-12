"""The Frappe handlers — the only module here that imports Frappe.

Six tools, and the shape of each is dictated by a primitive the core supplies
rather than by this extension's preference.

``semantic_search``
    Two stages, and the second is the whole security model: recall from the
    index, then filter every candidate through Frappe before anything is
    returned.  A record the caller may not read does not appear in the response
    at all — not downranked, not truncated, not marked.

``index_doctype`` / ``reindex_doctype``
    Queued work with a privilege snapshot.  Neither reads a provider inline,
    and neither runs inside a document hook — a hook that called an embedding
    provider would make a third-party API a dependency of every save on the
    site.

``index_drift``
    The drift detector, as a tool rather than only a test, because a drifted
    index is a silent wrong answer in production.

The lifecycle is complete
-------------------------
``after_insert``, ``on_update`` and ``on_trash`` all enqueue; ``reindex``
rebuilds; ``index_drift`` reports.  A hook set that covered inserts and updates
but not deletions would leave orphaned entries that a search returns and a
reader follows to nothing.
"""

from __future__ import annotations

from typing import Any

import frappe

from albanna_ai_chat.core.jobs import JobRequest, RetryPolicy, validate_job
from albanna_ai_chat.core.port import ToolContext, ToolResult
from albanna_ai_chat.core.tools import ToolSpec, compact_json, validate_arguments
from albanna_ai_chat.services.jobs import JobRunner, audit, enqueue, snapshot_for

from semantic_search_mcp.tools.retrieval import (
    MAX_CANDIDATES,
    Candidate,
    RetrievalProblem,
    SearchReport,
    build_report,
    drift_report,
    embed_text,
    validate_configured,
    validate_search,
)

INDEXED_DOCTYPE = "Indexed DocType"
SETTINGS_DOCTYPE = "Semantic Search MCP Settings"

#: The tools this backend can run.
TOOL_NAMES = frozenset(
    {
        "semantic_search",
        "list_indexed_doctypes",
        "index_drift",
        "index_doctype",
        "reindex_doctype",
        "unindex_doctype",
    }
)


class SemanticSearchBackend:
    """Runs this extension's six tools."""

    def handles(self, name: str) -> bool:
        return name in TOOL_NAMES

    def call(
        self,
        spec: ToolSpec,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        problem = validate_arguments(spec, arguments)
        if problem:
            return ToolResult.failure(problem, error="invalid_arguments")

        handler = getattr(self, f"_run_{spec.name}", None)
        if handler is None:  # pragma: no cover - handles() gates this
            return ToolResult.failure(
                f"{spec.name} is not implemented.", error="not_implemented"
            )
        return handler(arguments, context)

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def _run_semantic_search(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        problem = validate_search(
            arguments["search_text"], arguments["doctype"], arguments.get("limit") or 10
        )
        if problem:
            return ToolResult.failure(str(problem), error="invalid_search")

        doctype = arguments["doctype"]
        limit = arguments.get("limit") or 10

        # --- stage 1: recall.  Knows nothing about who is asking. ---------
        candidates = self._recall(arguments["search_text"], doctype, MAX_CANDIDATES)

        # --- stage 2: filter.  Frappe decides. ---------------------------
        permitted = self._permitted(doctype, [c.name for c in candidates])
        report = build_report(candidates, permitted, limit=limit)

        payload = {
            "query": arguments["search_text"],
            "doctype": doctype,
            "recalled": report.recalled,
            "permitted": report.permitted,
            "shown": report.shown,
            "results": [
                {"name": c.name, "score": c.score, "snippet": c.snippet}
                for c in report.results
            ],
        }
        return ToolResult.success(
            compact_json(payload), data=payload, summary=report.sentence()
        )

    def _run_list_indexed_doctypes(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        rows = frappe.get_list(
            INDEXED_DOCTYPE,
            fields=["doctype", "fields", "model_version", "record_count", "indexed_on"],
            limit_page_length=100,
        )
        return ToolResult.success(
            compact_json(rows),
            data=rows,
            summary=f"{len(rows)} DocType(s) indexed",
        )

    def _run_index_drift(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        wanted = arguments.get("doctype")
        rows = frappe.get_list(INDEXED_DOCTYPE, fields=["doctype"], limit_page_length=100)
        targets = [wanted] if wanted else [r["doctype"] for r in rows]

        reports = []
        for doctype in targets:
            rows = frappe.get_list(doctype, fields=["name"], limit_page_length=0) or []
            source = {r["name"] for r in rows}
            indexed = self._indexed_names(doctype)
            reports.append(drift_report(doctype, source, indexed))

        payload = [
            {
                "doctype": r.doctype,
                "missing": list(r.missing),
                "orphaned": list(r.orphaned),
                "clean": r.clean,
            }
            for r in reports
        ]
        dirty = [r for r in reports if not r.clean]
        return ToolResult.success(
            compact_json(payload),
            data=payload,
            summary=(
                f"{len(dirty)} of {len(reports)} index(es) have drifted"
                if dirty
                else "Every index matches its records."
            ),
        )

    # ------------------------------------------------------------------
    # writes — all queued
    # ------------------------------------------------------------------

    def _run_index_doctype(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        doctype = arguments["doctype"]
        configured = tuple(arguments.get("fields") or self._configured_fields(doctype))
        problem = validate_configured(configured)
        if problem:
            return ToolResult.failure(str(problem), error="bad_field_list")

        snapshot = snapshot_for(context.principal.user)
        request = JobRequest(
            extension="semantic_search_mcp",
            job="index_doctype",
            payload={"doctype": doctype, "fields": list(configured)},
            snapshot=snapshot,
            purpose=f"Embed {len(configured)} field(s) of every {doctype} record.",
            retry=RetryPolicy(attempts=2, backoff_seconds=120),
        )
        problem = validate_job(request)
        if problem:
            return ToolResult.failure(str(problem), error="invalid_job")

        queued = enqueue(
            request, "semantic_search_mcp.tools.indexing.run_index_doctype"
        )
        return ToolResult.success(
            compact_json(
                {
                    "status": "queued",
                    "job": request.key,
                    "fields": list(configured),
                    "note": (
                        "Indexing runs in the background. The DocType becomes "
                        "searchable by meaning when it finishes."
                    ),
                }
            ),
            summary=f"Queued indexing of {doctype}",
        )

    def _run_reindex_doctype(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        doctype = arguments["doctype"]
        if not frappe.db.exists(INDEXED_DOCTYPE, {"doctype": doctype}):
            return ToolResult.failure(
                f"{doctype} is not indexed — index it first.", error="not_indexed"
            )

        snapshot = snapshot_for(context.principal.user)
        request = JobRequest(
            extension="semantic_search_mcp",
            job="reindex_doctype",
            payload={"doctype": doctype},
            snapshot=snapshot,
            purpose=f"Re-embed every {doctype} record under the current model.",
            retry=RetryPolicy(attempts=1),
        )
        problem = validate_job(request)
        if problem:
            return ToolResult.failure(str(problem), error="invalid_job")

        enqueue(request, "semantic_search_mcp.tools.indexing.run_reindex_doctype")
        return ToolResult.success(
            compact_json({"status": "queued", "job": request.key}),
            summary=f"Queued re-indexing of {doctype}",
        )

    def _run_unindex_doctype(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        doctype = arguments["doctype"]
        if not frappe.db.exists(INDEXED_DOCTYPE, {"doctype": doctype}):
            return ToolResult.failure(
                f"{doctype} is not indexed.", error="not_indexed"
            )
        # The records are untouched; only the derived index goes.
        frappe.db.delete(INDEXED_DOCTYPE, {"doctype": doctype})
        self._drop_vectors(doctype)
        return ToolResult.success(
            compact_json({"status": "unindexed", "doctype": doctype}),
            summary=f"Stopped indexing {doctype}",
        )

    # ------------------------------------------------------------------
    # Frappe helpers
    # ------------------------------------------------------------------

    def _recall(self, query: str, doctype: str, limit: int) -> list[Candidate]:
        """Stage 1: ask the index for candidates.

        Deliberately unaware of who is asking.  The permission question is
        answered by :meth:`_permitted`, and answering it here would make the
        index a second source of truth about permissions.
        """
        store = self._vector_store()
        if store is None:
            return []
        return store.search(query, doctype, limit)

    def _permitted(self, doctype: str, names: list[str]) -> set[str]:
        """Stage 2: which of *names* the caller may read.

        Asked per record rather than per doctype, because "may read Customer
        somewhere" is not "may read this Customer" — the same distinction the
        visibility rule draws, applied to the target.
        """
        allowed = set()
        for name in names:
            try:
                if frappe.has_permission(doctype, "read", doc=name):
                    allowed.add(f"{doctype}/{name}")
            except Exception:  # noqa: BLE001 — a rule that cannot answer has not said yes
                frappe.log_error(title="semantic-search-mcp: permission check failed")
        return allowed

    def _indexed_names(self, doctype: str) -> set[str]:
        store = self._vector_store()
        if store is None:
            return set()
        return set(store.names(doctype))

    def _drop_vectors(self, doctype: str) -> None:
        store = self._vector_store()
        if store is not None:
            store.drop(doctype)

    def _vector_store(self):
        """The vector store, or ``None`` when none is configured.

        ``None`` is the honest answer: a site with no store is a site where
        every search returns nothing and says so, rather than half-working.
        """
        settings = self._settings()
        if not settings.get("vector_store"):
            return None
        from semantic_search_mcp.tools.store import vector_store

        return vector_store(settings)

    def _settings(self) -> dict:
        if not frappe.db.exists(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE):
            return {}
        doc = frappe.get_doc(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
        return {f: doc.get(f) for f in doc.meta.fields}

    def _configured_fields(self, doctype: str) -> list[str]:
        raw = self._settings().get("fields_by_doctype") or ""
        for line in str(raw).splitlines():
            if ":" not in line:
                continue
            name, fields = line.split(":", 1)
            if name.strip() == doctype:
                return [f.strip() for f in fields.split(",") if f.strip()]
        return []


# ---------------------------------------------------------------------------
# The jobs the worker runs
# ---------------------------------------------------------------------------


def run_index_doctype(request) -> Any:
    from albanna_ai_chat.services.jobs import run

    return run(request, _index_under_snapshot)


def run_reindex_doctype(request) -> Any:
    from albanna_ai_chat.services.jobs import run

    return run(request, _index_under_snapshot)


def _index_under_snapshot(runner: JobRunner):
    """Embed every record of the target doctype, under the caller's snapshot.

    The snapshot bounds what may be *written to the index*, and the records are
    read as the job principal — which is why the operator, not the extension,
    decides which doctypes are indexed.  Indexing another app's data is an
    operator decision.
    """
    payload = runner.request.payload
    doctype = payload["doctype"]
    fields = list(payload["fields"])

    problem = validate_configured(fields)
    if problem:
        return runner.outcome(False, "The configured field list is not valid.", str(problem))

    store = _store_for(runner)
    if store is None:
        return runner.outcome(
            False, "No vector store is configured.", "not_configured"
        )

    names = [
        r["name"]
        for r in frappe.get_list(doctype, fields=["name"], limit_page_length=0) or []
    ]
    embedded = 0
    for name in names:
        doc = frappe.get_doc(doctype, name)
        text = embed_text({f: doc.get(f) for f in fields}, fields)
        if not text:
            continue
        store.upsert(doctype, name, text)
        embedded += 1

    _record_indexed(doctype, fields, len(names))
    audit(
        runner.request,
        runner.outcome(True, f"Indexed {embedded} record(s)", changed=(doctype,)),
    )
    return runner.outcome(True, f"Indexed {embedded} record(s)", changed=(doctype,))


def _store_for(runner: JobRunner):
    from semantic_search_mcp.tools.indexing import SemanticSearchBackend

    return SemanticSearchBackend()._vector_store()


def _record_indexed(doctype: str, fields: list[str], count: int) -> None:
    """Remember what is indexed, so drift has something to compare against."""
    settings = SemanticSearchBackend()._settings()
    model = str(settings.get("embedding_model") or "")
    if frappe.db.exists(INDEXED_DOCTYPE, {"doctype": doctype}):
        doc = frappe.get_doc(INDEXED_DOCTYPE, {"doctype": doctype})
        doc.db_set(
            {
                "fields": ", ".join(fields),
                "model_version": model,
                "record_count": count,
            }
        )
        return
    frappe.get_doc(
        {
            "doctype": INDEXED_DOCTYPE,
            "doctype_name": doctype,
            "fields": ", ".join(fields),
            "model_version": model,
            "record_count": count,
        }
    ).insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Document hooks — every one of them enqueues and returns
# ---------------------------------------------------------------------------
#
# A hook that called an embedding provider inline would make a third-party API
# a dependency of every save on the site: a slow provider makes every write
# slow, and a down provider makes every write *fail*, because a hook that
# raises rolls back the transaction.  So each of these enqueues and returns,
# and the core's validator refuses an extension that reaches past it for a
# provider.


def on_document_change(doctype: str, name: str, method: str) -> None:
    """Queue a re-embed of one record after it changed.

    Called from ``after_insert`` and ``on_update``.  It does not read the
    record, does not call a provider, and does not raise — a queue failure is
    logged, not propagated, because a failed enqueue must not fail the save
    that triggered it.
    """
    try:
        snapshot = snapshot_for(frappe.session.user)
        request = JobRequest(
            extension="semantic_search_mcp",
            job="embed_record",
            payload={"doctype": doctype, "name": name, "method": method},
            snapshot=snapshot,
            purpose=f"Re-embed {doctype}/{name} after it changed.",
            retry=RetryPolicy(attempts=2, backoff_seconds=60),
        )
        if validate_job(request) is not None:
            return
        enqueue(request, "semantic_search_mcp.tools.indexing.run_embed_record")
    except Exception:  # noqa: BLE001 — a failed enqueue must not fail the save
        frappe.log_error(title="semantic-search-mcp: enqueue after change failed")


def on_document_trash(doctype: str, name: str) -> None:
    """Remove one record's vectors after it was deleted.

    Without this the index keeps an entry whose record is gone, and a search
    returns it, and the reader follows a link to nothing.  Deletion is the half
    of the lifecycle that is easiest to forget and most visible when it is.
    """
    try:
        store = SemanticSearchBackend()._vector_store()
        if store is not None:
            store.drop_one(doctype, name)
    except Exception:  # noqa: BLE001
        frappe.log_error(title="semantic-search-mcp: drop after trash failed")


def run_embed_record(request) -> Any:
    from albanna_ai_chat.services.jobs import run

    return run(request, _embed_one_under_snapshot)


def _embed_one_under_snapshot(runner: JobRunner):
    payload = runner.request.payload
    doctype, name = payload["doctype"], payload["name"]
    if not frappe.db.exists(doctype, name):
        # The record went away between the enqueue and the run.  Dropping the
        # stale vector is the correct outcome, not a failure.
        store = _store_for(runner)
        if store is not None:
            store.drop_one(doctype, name)
        return runner.outcome(True, f"{doctype}/{name} no longer exists; vector dropped.")

    doc = frappe.get_doc(doctype, name)
    fields = SemanticSearchBackend()._configured_fields(doctype)
    text = embed_text({f: doc.get(f) for f in fields}, fields)
    store = _store_for(runner)
    if store is None:
        return runner.outcome(False, "No vector store is configured.", "not_configured")
    if text:
        store.upsert(doctype, name, text)
    return runner.outcome(True, f"Re-embedded {doctype}/{name}")


__all__ = [
    "RetrievalProblem",
    "SearchReport",
    "SemanticSearchBackend",
    "on_document_change",
    "on_document_trash",
    "run_embed_record",
    "run_index_doctype",
    "run_reindex_doctype",
]
