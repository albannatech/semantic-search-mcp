"""Indexed DocType — one DocType this site indexes for semantic search."""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class IndexedDocType(Document):
    """Derived data. The index is a recall optimisation and never an
    authorisation boundary; ``index_drift`` compares it against the source
    records in both directions."""
