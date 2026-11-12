"""Semantic Search Mcp Settings — the Single row this extension is configured with."""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class SemanticSearchMcpSettings(Document):
    """One settings row, created by the installer.

    A Single doctype's controller exists so the row can carry defaults and
    validation; it is not a place for business rules.
    """
