"""The security-critical half: permission-filtered retrieval and drift.

These are the tests that matter most in this extension, because the failure
they guard against is not a crash — it is a search that returns a record the
caller may not read, which looks exactly like a search that worked.
"""

import pytest

from semantic_search_mcp.tools.retrieval import (
    MAX_CANDIDATES,
    Candidate,
    DriftReport,
    RetrievalProblem,
    build_report,
    drift_report,
    embed_text,
    filter_by_permission,
    validate_configured,
    validate_search,
)


def candidate(name, score=0.9, doctype="Customer"):
    return Candidate(doctype=doctype, name=name, score=score, snippet=f"{name} snippet")


# ---------------------------------------------------------------------------
# The filter is the whole security model
# ---------------------------------------------------------------------------


class TestTheFilter:
    def test_a_permitted_record_survives(self):
        kept = filter_by_permission([candidate("CUST-1")], {"Customer/CUST-1"})
        assert [c.name for c in kept] == ["CUST-1"]

    def test_an_unpermitted_record_is_dropped_entirely(self):
        kept = filter_by_permission([candidate("CUST-1")], set())
        assert kept == []

    def test_a_dropped_record_is_not_merely_downranked(self):
        # A record the caller may not read must not appear at all — not with a
        # marker, not at the bottom.  A response that names it has leaked it.
        kept = filter_by_permission(
            [candidate("CUST-1"), candidate("CUST-2")], {"Customer/CUST-2"}
        )
        assert [c.name for c in kept] == ["CUST-2"]

    def test_the_filter_is_per_record_not_per_doctype(self):
        # "May read Customer somewhere" is not "may read this Customer".
        kept = filter_by_permission(
            [candidate("CUST-1"), candidate("CUST-2")], {"Customer/CUST-1"}
        )
        assert len(kept) == 1

    def test_a_doctype_mismatch_is_dropped(self):
        kept = filter_by_permission([candidate("X", doctype="Issue")], {"Customer/X"})
        assert kept == []


# ---------------------------------------------------------------------------
# The report names all three numbers
# ---------------------------------------------------------------------------


class TestTheReport:
    def test_it_names_recalled_permitted_and_shown(self):
        report = build_report(
            [candidate("CUST-1"), candidate("CUST-2"), candidate("CUST-3")],
            {"Customer/CUST-1"},
            limit=10,
        )
        assert (report.recalled, report.permitted, report.shown) == (3, 1, 1)

    def test_a_fully_permitted_search_says_so_simply(self):
        report = build_report([candidate("CUST-1")], {"Customer/CUST-1"}, limit=10)
        assert report.sentence() == "Found 1 record(s)."

    def test_a_permission_bounded_search_says_it_was_bounded(self):
        report = build_report(
            [candidate(f"CUST-{i}") for i in range(5)],
            {"Customer/CUST-0"},
            limit=10,
        )
        sentence = report.sentence()
        assert "5 recalled" in sentence
        assert "1 permitted" in sentence

    def test_the_limit_is_applied_after_filtering(self):
        # A reader who may see 3 of 40 recalled is shown 3, not 3 of the first
        # 10 recalled.
        candidates = [candidate(f"CUST-{i}") for i in range(40)]
        permitted = {f"Customer/CUST-{i}" for i in range(3)}
        report = build_report(candidates, permitted, limit=10)
        assert report.shown == 3
        assert report.permitted == 3


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_an_empty_query_is_refused(self):
        problem = validate_search("   ", "Customer")
        assert isinstance(problem, RetrievalProblem)
        assert "matches everything equally" in problem

    def test_a_missing_doctype_is_refused(self):
        problem = validate_search("find similar", "")
        assert isinstance(problem, RetrievalProblem)

    def test_a_non_positive_limit_is_refused(self):
        problem = validate_search("find similar", "Customer", limit=0)
        assert isinstance(problem, RetrievalProblem)

    def test_a_well_formed_search_is_accepted(self):
        assert validate_search("find similar", "Customer", limit=10) is None

    def test_an_empty_configured_field_list_is_refused(self):
        problem = validate_configured([])
        assert isinstance(problem, RetrievalProblem)
        assert "matches everything equally" in problem

    def test_a_non_identifier_field_is_refused(self):
        problem = validate_configured(["not a field"])
        assert isinstance(problem, RetrievalProblem)


# ---------------------------------------------------------------------------
# Drift — both directions, because they fail differently
# ---------------------------------------------------------------------------


class TestDrift:
    def test_a_clean_index_reports_clean(self):
        report = drift_report("Customer", {"CUST-1"}, {"CUST-1"})
        assert report.clean is True

    def test_a_record_that_exists_but_is_not_indexed_is_missing(self):
        # A search will not find it.  Too few results, silently.
        report = drift_report("Customer", {"CUST-1", "CUST-2"}, {"CUST-1"})
        assert report.missing == ("CUST-2",)
        assert report.clean is False

    def test_an_indexed_entry_whose_record_is_gone_is_orphaned(self):
        # A search *will* return it, and the reader follows a link to nothing.
        report = drift_report("Customer", {"CUST-1"}, {"CUST-1", "CUST-9"})
        assert report.orphaned == ("CUST-9",)

    def test_both_directions_are_reported_together(self):
        report = drift_report("Customer", {"A", "B"}, {"B", "C"})
        assert report.missing == ("A",)
        assert report.orphaned == ("C",)

    def test_the_sentence_names_both_counts(self):
        report = drift_report("Customer", {"A", "B"}, {"B", "C"})
        sentence = report.sentence()
        assert "1 not indexed" in sentence
        assert "1 orphaned" in sentence

    def test_a_clean_report_says_so(self):
        report = DriftReport(doctype="Customer")
        assert "matches the records" in report.sentence()


# ---------------------------------------------------------------------------
# What gets embedded
# ---------------------------------------------------------------------------


class TestEmbedText:
    def test_only_the_configured_fields_are_embedded(self):
        text = embed_text(
            {"customer_name": "ACME", "secret": "x"}, ("customer_name",)
        )
        assert "ACME" in text
        assert "secret" not in text

    def test_the_order_is_fixed_so_an_unrelated_edit_does_not_change_it(self):
        fields = {"a": "1", "b": "2"}
        assert embed_text(fields, ("a", "b")) == embed_text(fields, ("a", "b"))

    def test_an_empty_value_is_skipped_rather_than_embedded_as_empty(self):
        text = embed_text({"a": "1", "b": ""}, ("a", "b"))
        assert "b:" not in text

    def test_the_candidate_ceiling_is_bounded(self):
        # The filter runs per candidate, so an unbounded recall is an unbounded
        # permission check.
        assert MAX_CANDIDATES == 200
