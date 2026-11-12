"""The vector store port — where embeddings live, without saying which store.

A port rather than an import, for the same reason the core's provider layer is
one: the *shape* of a vector store is small and stable (upsert, search, names,
drop) and the differences between stores are mechanical.  Putting the shape
here means a new store is one small module rather than a change to every
handler, and the handlers can be tested against a store that answers
deterministically.

What a store must never be asked
--------------------------------
Whether a caller may read a record.  A store that answered that question would
be a second source of truth about permissions, and second sources of truth are
wrong eventually.  :meth:`SemanticSearchBackend._permitted` asks Frappe, per
record, after retrieval — and that is the only place the question is answered.

The in-memory store
-------------------
:class:`MemoryStore` is the default and it is deliberately useless in
production: it lives in one process, so a bench with several workers has
several indexes that disagree.  It exists so the extension is testable and so
a site with no store configured fails with a sentence an operator can act on,
rather than half-working.  A real deployment configures one of the backed
stores and nothing else changes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


class StoreProblem(str):
    """A store operation that could not be completed."""


@dataclass
class Entry:
    """One indexed record."""

    doctype: str
    name: str
    text: str
    #: A bag-of-words vector.  Crude on purpose: the in-memory store exists to
    #: prove the shape, not to be good at search.
    vector: dict[str, float] = field(default_factory=dict)


def _vectorise(text: str) -> dict[str, float]:
    """A term-frequency vector for *text*.

    Deliberately the simplest thing that works.  A real store embeds with a
    model; this one counts words, which is enough to exercise upsert, search,
    names and drop without a network call.
    """
    counts: dict[str, float] = {}
    for token in str(text or "").lower().split():
        token = token.strip(".,;:!?()[]{}\"'")
        if token:
            counts[token] = counts.get(token, 0.0) + 1.0
    return counts


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    dot = sum(left[t] * right[t] for t in shared)
    norm_l = math.sqrt(sum(v * v for v in left.values()))
    norm_r = math.sqrt(sum(v * v for v in right.values()))
    if norm_l == 0 or norm_r == 0:
        return 0.0
    return dot / (norm_l * norm_r)


class MemoryStore:
    """An in-memory store.  One process, several workers, several indexes.

    Documented as unsuitable for production rather than quietly assumed to be
    fine: a bench with several workers would give each its own index, and a
    search would answer differently depending on which worker took it.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], Entry] = {}

    def upsert(self, doctype: str, name: str, text: str) -> None:
        self._entries[(doctype, name)] = Entry(
            doctype=doctype, name=name, text=text, vector=_vectorise(text)
        )

    def search(self, query: str, doctype: str, limit: int) -> list:
        """Candidates for *query*, best first.  No permission question asked."""
        from semantic_search_mcp.tools.retrieval import Candidate

        query_vector = _vectorise(query)
        scored = []
        for (entry_doctype, _name), entry in self._entries.items():
            if entry_doctype != doctype:
                continue
            scored.append((_cosine(query_vector, entry.vector), entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            Candidate(
                doctype=entry.doctype,
                name=entry.name,
                score=round(score, 4),
                snippet=entry.text[:200],
            )
            for score, entry in scored[:limit]
            if score > 0
        ]

    def names(self, doctype: str) -> list[str]:
        return [
            name for (entry_doctype, name) in self._entries if entry_doctype == doctype
        ]

    def drop(self, doctype: str) -> None:
        for key in [k for k in self._entries if k[0] == doctype]:
            del self._entries[key]

    def drop_one(self, doctype: str, name: str) -> None:
        self._entries.pop((doctype, name), None)


def vector_store(settings: dict):
    """The store named in *settings*, or ``None`` when none is configured.

    ``None`` is the honest answer: a site with no store is a site where every
    search returns nothing and says so.
    """
    kind = str(settings.get("vector_store") or "").strip()
    if not kind:
        return None
    if kind == "memory":
        return MemoryStore()
    # A backed store (Qdrant, pgvector, …) is configured here by name.  Until
    # one is configured the extension reports "no store" rather than pretending.
    return None
