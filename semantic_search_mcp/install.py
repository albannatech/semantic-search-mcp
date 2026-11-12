"""The installer — what has to exist before this extension is usable.

Two things, both idempotent, because a restored site repairs itself on the next
migrate rather than failing on a missing document the first time somebody opens
the widget.

The settings row ships **empty**: no vector store, no embedding model, no
indexed doctypes, no configured fields.  That is the whole of the "field
selection is explicit, PII-aware, and defaults to nothing" rule — an extension
that installed itself ready to embed every field of every doctype would be an
extension that made a decision the operator never made.
"""

from __future__ import annotations

import frappe

ROLE = "Semantic Search MCP User"
SETTINGS = "Semantic Search MCP Settings"


def after_install() -> None:
    _ensure_role()
    _ensure_settings()


def after_migrate() -> None:
    """Called on every migrate, so a restored site repairs itself."""
    _ensure_role()
    _ensure_settings()


def _ensure_role() -> None:
    if frappe.db.exists("Role", ROLE):
        return
    frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(
        ignore_permissions=True
    )


def _ensure_settings() -> None:
    """The Single settings row, empty by default.

    ``fields_by_doctype`` is empty and there is no default set: an operator
    writes one line per DocType they want indexed, naming the fields.  A field
    nobody chose is not indexed, which is the default rather than the
    exception.
    """
    if frappe.db.exists(SETTINGS, SETTINGS):
        return
    frappe.get_doc(
        {
            "doctype": SETTINGS,
            # No store until configured.  A site with none returns nothing and
            # says so, rather than half-working.
            "vector_store": "",
            "embedding_model": "",
            # One line per DocType: ``Customer: customer_name, territory``.
            # Empty until an operator writes it.
            "fields_by_doctype": "",
            # Fields never embedded, whatever else is configured.  Money,
            # identity, anything an operator marks sensitive.
            "excluded_fields": "",
            # How many candidates one search pulls before filtering.
            "max_candidates": 200,
        }
    ).insert(ignore_permissions=True)
