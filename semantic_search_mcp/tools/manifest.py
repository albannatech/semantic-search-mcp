"""What this extension contributes to the core — the whole contract.

An extension is a Frappe app that implements the ``frappe_mcp_tools`` hook.
This module is what that hook points at: ``TOOLS`` (a list of ``ToolSpec``)
and ``TIERS`` (every name classified).  The core validates both atomically and
absorbs them tagged with this app's name, so uninstalling removes exactly these
tools and nothing else.

Why the surface looks like this
-------------------------------
Six tools, no flow.  Indexing is queued work whose result is a *derived* index,
so there is no transaction to guard — and the one operation that could have
been a flow, "embed and then search", is two calls separated by a queue.

The load-bearing decision is not in this file
---------------------------------------------
**The index is a recall optimisation, never an authorisation boundary.**
``semantic_search`` retrieves candidates from a vector store that has no
permission engine, and then filters every candidate through Frappe before it is
returned.  A user who cannot see a Customer does not get that Customer in the
results — and the residual inference channel (that a near-match *exists*) is
documented as accepted risk rather than pretended away.

Nothing here imports Frappe
---------------------------
The manifest is the contract, and the contract must be importable on a machine
with none of this app's dependencies installed — that is what lets the core
validate it before anything runs.  The handlers live in ``indexing.py``.
"""

from __future__ import annotations

from albanna_ai_chat.core.flow import CompensationSpec
from albanna_ai_chat.core.tools import READ, WRITE, ToolSpec

#: The DocTypes this extension owns.  One module, and the doctypes its domain
#: actually has — not one doctype per tool.
INDEXED_DOCTYPE = "Indexed DocType"
SETTINGS = "Semantic Search MCP Settings"


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------

SEMANTIC_SEARCH = ToolSpec(
    name="semantic_search",
    description=(
        "Find records by meaning rather than by exact match. Use this for "
        "'find customers like this one', 'tickets about billing problems', "
        "'which supplier did we have this issue with before'. Returns records "
        "the caller is permitted to see — a record they may not read is never "
        "returned, and never counted. Always say how many were found and how "
        "many were filtered out, so the reader knows the search was bounded by "
        "their permissions rather than by the data."
    ),
    parameters=_schema(
        {
            "search_text": {
                "type": "string",
                "description": "What to look for, in the reader's own words.",
            },
            "doctype": {
                "type": "string",
                "description": "The DocType to search, e.g. 'Customer', 'Issue'.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "How many records to return (default 10).",
            },
        },
        ["search_text", "doctype"],
    ),
    tier=READ,
)

LIST_INDEXED_DOCTYPES = ToolSpec(
    name="list_indexed_doctypes",
    description=(
        "List the DocTypes this site indexes for semantic search, with the "
        "fields each one embeds, the embedding model version, and when it was "
        "last indexed. Use this to answer 'what can I search by meaning'."
    ),
    parameters=_schema({}, []),
    tier=READ,
)

INDEX_DRIFT = ToolSpec(
    name="index_drift",
    description=(
        "Report where the index has drifted from the source records: records "
        "that exist but are not indexed, and indexed entries whose record is "
        "gone. Use this when a search returns fewer results than expected, or "
        "after a bulk update, an import or a patch. A drifted index is a "
        "silent wrong answer, so this is a tool and not only a test."
    ),
    parameters=_schema(
        {
            "doctype": {
                "type": "string",
                "description": "The DocType to check. Omit to check every indexed one.",
            }
        },
        [],
    ),
    tier=READ,
)


# --------------------------------------------------------------------------
# Writes — all queued, none inline
# --------------------------------------------------------------------------

INDEX_DOCTYPE = ToolSpec(
    name="index_doctype",
    description=(
        "Start indexing a DocType's records for semantic search. The work runs "
        "in the background — this call returns immediately and nothing is "
        "embedded yet. Only the fields the operator configured are embedded; a "
        "field nobody chose is not indexed, which is the default rather than "
        "the exception."
    ),
    parameters=_schema(
        {
            "doctype": {
                "type": "string",
                "description": "The DocType to index, e.g. 'Customer'.",
            },
            "fields": {
                "type": "array",
                "description": (
                    "The fieldnames to embed. Omit to use the configured set. "
                    "An empty list is refused — indexing nothing spends an "
                    "embedding call and produces an index that matches "
                    "everything equally."
                ),
                "items": {"type": "string"},
            },
        },
        ["doctype"],
    ),
    tier=WRITE,
    doctype_arg="doctype",
    write_permission="create",
    # Indexing adds; unindexing removes.  A genuine inverse pair.
    compensation=CompensationSpec(mechanism="reverse", undo_with=("unindex_doctype",)),
)

REINDEX_DOCTYPE = ToolSpec(
    name="reindex_doctype",
    description=(
        "Re-embed every record of an indexed DocType. Use this after the "
        "embedding model changes, because a vector store holding embeddings "
        "from two models is a store that answers inconsistently. The work runs "
        "in the background."
    ),
    parameters=_schema(
        {"doctype": {"type": "string", "description": "The DocType to re-index."}},
        ["doctype"],
    ),
    tier=WRITE,
    doctype_arg="doctype",
    write_permission="write",
    # Not destructive, and it says why: the index is derived data and the
    # source records are untouched, so there is nothing to undo — only
    # something to rebuild.
    compensation=CompensationSpec(
        mechanism="none",
        reason=(
            "Re-embedding replaces derived vectors. The source records are "
            "untouched, so there is nothing to undo — only something to "
            "rebuild, which is what this tool does."
        ),
    ),
)

UNINDEX_DOCTYPE = ToolSpec(
    name="unindex_doctype",
    description=(
        "Stop indexing a DocType and remove its vectors. Use this when a "
        "DocType should no longer be searchable by meaning. The records "
        "themselves are not touched — only the derived index is removed."
    ),
    parameters=_schema(
        {"doctype": {"type": "string", "description": "The DocType to un-index."}},
        ["doctype"],
    ),
    tier=WRITE,
    doctype_arg="doctype",
    write_permission="delete",
    compensation=CompensationSpec(
        mechanism="none",
        reason=(
            "Removing an index is recoverable by indexing again, but that is a "
            "fresh build rather than an undo — the vectors are gone until it "
            "finishes."
        ),
    ),
)


#: Every tool this extension contributes.
TOOLS = [
    SEMANTIC_SEARCH,
    LIST_INDEXED_DOCTYPES,
    INDEX_DRIFT,
    INDEX_DOCTYPE,
    REINDEX_DOCTYPE,
    UNINDEX_DOCTYPE,
]

#: Every tool classified.  The validator refuses a tool that is not here, so
#: this dict and TOOLS are kept in step by a test rather than by memory.
TIERS = {
    "semantic_search": READ,
    "list_indexed_doctypes": READ,
    "index_drift": READ,
    "index_doctype": WRITE,
    "reindex_doctype": WRITE,
    "unindex_doctype": WRITE,
}

#: The core version range this extension is built against.
#:
#: ``0.3.0`` is the first core version carrying the async primitive this
#: extension depends on — embedding is queued work with a privilege snapshot,
#: never an inline call in a document hook.  An earlier core has no such
#: primitive, so declaring a lower bound below 0.3.0 would promise a
#: compatibility that cannot exist.
REQUIRES_CORE = ">=0.4.0,<0.5.0"
