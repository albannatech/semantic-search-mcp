"""Retrieval, filtering and drift — pure, and tested without a site.

This module holds the part of semantic search that is actually dangerous, and
it holds it in Frappe-free code so it can be tested without a bench.

The two-stage rule
------------------
A vector store has no permission engine.  Query it and you get the nearest
neighbours — including records the caller may not read.  So retrieval is two
stages, and the second one is not optional:

1. **Recall** — ask the index for candidates.  This stage knows nothing about
   who is asking.
2. **Filter** — ask Frappe what the caller may see, and drop everything else.

The filter is the whole of the security model here, and it is applied *after*
retrieval rather than instead of it, because an index that could answer the
permission question would be an index that had to be kept in sync with the
permission matrix — which is a second source of truth, and second sources of
truth are wrong eventually.

What the filter does not close
------------------------------
The *existence* of a near-match leaks information: a user who cannot see a
Customer can still learn that a customer matching their query exists, and
approximately what it says.  Filtering the results does not close that channel,
and nothing available to this extension does.  It is documented as accepted
risk in the ADR rather than pretended away — a design that claims to have
solved it is a design nobody should trust.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The most candidates one search pulls from the index before filtering.
#:
#: Bounded, because the filter runs per candidate and an unbounded recall is an
#: unbounded permission check.  A search that needs more than this is a search
#: that should be narrowed.
MAX_CANDIDATES = 200


class RetrievalProblem(str):
    """A search the core refuses, as a sentence a developer can act on."""


@dataclass(frozen=True)
class Candidate:
    """One record the index returned, before permissions are consulted."""

    doctype: str
    name: str
    #: The index's own similarity score.  Advisory only — it says nothing about
    #: whether the caller may see the record.
    score: float = 0.0
    #: The text that was embedded, for the panel's one-line summary.
    snippet: str = ""


@dataclass(frozen=True)
class SearchReport:
    """What a search found, in the three numbers a reader needs.

    ``recalled`` is what the index returned.  ``permitted`` is what survived
    the permission filter.  ``shown`` is what is being displayed.  A search
    that reports only ``shown`` hides the fact that permissions bounded it —
    which reads as "there is nothing else" when the truth is "there is plenty
    you may not see".
    """

    recalled: int
    permitted: int
    shown: int
    results: tuple[Candidate, ...] = ()

    def sentence(self) -> str:
        """One line for the panel, naming all three numbers."""
        if self.recalled == self.permitted == self.shown:
            return f"Found {self.shown} record(s)."
        return (
            f"Found {self.shown} of {self.permitted} permitted record(s) "
            f"({self.recalled} recalled from the index)."
        )


def filter_by_permission(
    candidates: "list[Candidate] | tuple[Candidate, ...]",
    permitted: "set[str] | frozenset[str]",
) -> list[Candidate]:
    """Drop every candidate the caller may not see.

    *permitted* is the set of ``doctype/name`` the caller may read, computed by
    Frappe.  A candidate whose name is not in it is dropped — not downranked,
    not truncated, not returned with a marker.  A record the caller may not
    read must not appear in the response at all, because a response that names
    it has already leaked it.
    """
    allowed = set(permitted)
    return [c for c in candidates if f"{c.doctype}/{c.name}" in allowed]


def build_report(
    candidates: "list[Candidate] | tuple[Candidate, ...]",
    permitted: "set[str] | frozenset[str]",
    limit: int = 10,
) -> SearchReport:
    """Filter *candidates* and report all three numbers.

    ``shown`` is capped at *limit*, and the cap is applied *after* filtering —
    so a reader who may see 3 of 40 recalled records is shown 3, not 3 of the
    first 10 recalled.
    """
    recalled = len(candidates)
    visible = filter_by_permission(candidates, permitted)
    shown = visible[: max(limit, 0)]
    return SearchReport(
        recalled=recalled,
        permitted=len(visible),
        shown=len(shown),
        results=tuple(shown),
    )


def validate_search(query: str, doctype: str, limit: int = 10) -> RetrievalProblem | None:
    """Check a search before the index is consulted.

    An empty query is refused: an embedding of nothing matches everything
    equally, which is a search that returns the whole index and calls it
    relevant.
    """
    if not str(query or "").strip():
        return RetrievalProblem(
            "a semantic search needs something to look for — an empty query "
            "matches everything equally"
        )
    if not str(doctype or "").strip():
        return RetrievalProblem("a semantic search must name the DocType to search")
    if limit < 1:
        return RetrievalProblem(f"a search needs a positive limit, got {limit}")
    return None


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftReport:
    """Where the index and the source records disagree.

    ``missing`` are records that exist but are not indexed — a search will not
    find them.  ``orphaned`` are indexed entries whose record is gone — a
    search *will* return them, and the reader will follow a link to nothing.
    Both are silent wrong answers, which is why this is a tool and not only a
    test.
    """

    doctype: str
    missing: tuple[str, ...] = ()
    orphaned: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.missing and not self.orphaned

    def sentence(self) -> str:
        if self.clean:
            return f"{self.doctype}: index matches the records."
        parts = []
        if self.missing:
            parts.append(f"{len(self.missing)} not indexed")
        if self.orphaned:
            parts.append(f"{len(self.orphaned)} orphaned")
        return f"{self.doctype}: " + ", ".join(parts) + "."


def drift_report(
    doctype: str,
    source_names: "set[str] | frozenset[str] | list[str]",
    indexed_names: "set[str] | frozenset[str] | list[str]",
) -> DriftReport:
    """Compare the source records against the index, in both directions.

    Both directions matter and they fail differently: a missing record is a
    search that returns too little, and an orphaned entry is a search that
    returns something that no longer exists.
    """
    source = set(source_names)
    indexed = set(indexed_names)
    return DriftReport(
        doctype=doctype,
        missing=tuple(sorted(source - indexed)),
        orphaned=tuple(sorted(indexed - source)),
    )


def embed_text(fields: dict, configured: "tuple[str, ...] | list[str]") -> str:
    """The text one record is embedded as.

    Only the *configured* fields, in a fixed order, joined by a separator that
    cannot appear in a value.  A record embedded from "whatever fields happened
    to be present" is a record whose embedding changes when an unrelated field
    is added — which is a search that answers differently after an unrelated
    edit.
    """
    parts = []
    for fieldname in configured:
        value = fields.get(fieldname)
        if value in (None, ""):
            continue
        parts.append(f"{fieldname}: {value}")
    return " | ".join(parts)


def validate_configured(configured: "tuple[str, ...] | list[str]") -> RetrievalProblem | None:
    """Check the configured field list before anything is embedded.

    An empty list is refused: indexing nothing spends an embedding call and
    produces an index that matches everything equally.
    """
    if not configured:
        return RetrievalProblem(
            "an index must name at least one field — indexing nothing spends "
            "an embedding call and produces an index that matches everything "
            "equally"
        )
    for fieldname in configured:
        if not str(fieldname).isidentifier():
            return RetrievalProblem(f"{fieldname!r} is not a valid fieldname")
    return None
