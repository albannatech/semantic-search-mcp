"""The extension contract, validated against the real core.

These tests import ``albanna_ai_chat`` — the core — and run this extension's
manifest through the core's own validator.  That is the point: a reference
extension that only tests itself proves nothing about whether the seam works.

The security-critical half is in ``test_retrieval.py``; this file is the
contract.
"""

import pathlib
import sys
import types

import pytest

CORE = pathlib.Path(__file__).resolve().parent.parent.parent / "albanna-ai-chat"
if CORE.is_dir() and str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

albanna_ai_chat = pytest.importorskip("albanna_ai_chat")

from albanna_ai_chat.core.file_tools import CORE_TOOLS  # noqa: E402
from albanna_ai_chat.core.flow import MECHANISMS  # noqa: E402
from albanna_ai_chat.core.jobs import provider_imports  # noqa: E402
from albanna_ai_chat.core.tools import READ, WRITE  # noqa: E402
from albanna_ai_chat.core.validate import (  # noqa: E402
    ContractViolation,
    absorb,
    validate_module,
)

from semantic_search_mcp.agent import (  # noqa: E402
    SEMANTIC_SEARCH_AGENT,
    system_prompt,
)
from semantic_search_mcp.tools import manifest  # noqa: E402


def module():
    mod = types.ModuleType("semantic_search_mcp.tools.manifest")
    mod.TOOLS = manifest.TOOLS
    mod.TIERS = manifest.TIERS
    return mod


@pytest.fixture(scope="module")
def specs():
    return validate_module(module(), known_tools=set(CORE_TOOLS.names()))


@pytest.fixture(scope="module")
def registry(specs):
    from dataclasses import replace

    tagged = [replace(spec, source="semantic_search_mcp") for spec in specs]
    return absorb(tagged, CORE_TOOLS)


# --------------------------------------------------------------------------
# The contract validates
# --------------------------------------------------------------------------


class TestTheContractValidates:
    def test_the_core_accepts_every_tool(self, specs):
        assert len(specs) == len(manifest.TOOLS)

    def test_every_tool_is_classified(self, specs):
        for spec in specs:
            assert spec.tier in (READ, WRITE), spec.name

    def test_tiers_and_tools_agree(self, specs):
        assert {s.name for s in specs} == set(manifest.TIERS)

    def test_no_tool_shadows_a_core_tool(self, specs):
        assert not ({s.name for s in specs} & set(CORE_TOOLS.names()))

    def test_absorption_is_atomic_and_returns_a_new_registry(self, registry):
        assert len(registry) == len(CORE_TOOLS) + len(manifest.TOOLS)
        assert len(CORE_TOOLS) == 3, "the core's own surface must not have grown"


class TestProvenance:
    def test_every_tool_is_tagged_with_this_app(self, registry):
        assert [s.name for s in registry.from_source("semantic_search_mcp")] == [
            s.name for s in manifest.TOOLS
        ]

    def test_uninstalling_removes_exactly_these_tools(self, registry):
        from albanna_ai_chat.core.toolset import without_source

        remaining = without_source(registry, "semantic_search_mcp")
        assert set(remaining.names()) == set(CORE_TOOLS.names())


# --------------------------------------------------------------------------
# Every write says how it is undone
# --------------------------------------------------------------------------


class TestCompensation:
    def test_every_write_declares_one(self, specs):
        for spec in specs:
            if spec.mutating:
                assert spec.compensation is not None, spec.name

    def test_every_mechanism_is_one_the_core_knows(self, specs):
        for spec in specs:
            if spec.compensation:
                assert spec.compensation.mechanism in MECHANISMS, spec.name

    def test_an_irreversible_action_says_why(self, specs):
        for spec in specs:
            if spec.compensation and spec.compensation.mechanism == "none":
                assert spec.compensation.reason.strip(), spec.name

    def test_the_undo_tools_actually_exist(self, specs, registry):
        for spec in specs:
            if not spec.compensation:
                continue
            for undo in spec.compensation.undo_with:
                assert undo in registry, f"{spec.name} names {undo}, which is not a tool"


# --------------------------------------------------------------------------
# No flow — indexing is queued work whose result is derived data
# --------------------------------------------------------------------------


class TestNoFlow:
    def test_there_are_no_flows(self, specs):
        assert [s.name for s in specs if s.is_flow] == []


# --------------------------------------------------------------------------
# The core version range
# --------------------------------------------------------------------------


class TestRequiresCore:
    def test_it_declares_one(self):
        assert manifest.REQUIRES_CORE

    def test_the_lower_bound_is_the_first_core_with_the_async_primitive(self):
        assert manifest.REQUIRES_CORE.startswith(">=0.3.0")

    def test_the_upper_bound_pins_the_minor(self):
        assert manifest.REQUIRES_CORE.endswith("<0.4.0")


# --------------------------------------------------------------------------
# The hook prohibition
# --------------------------------------------------------------------------


class TestHookProhibition:
    def test_the_handlers_import_no_provider(self):
        import inspect

        from semantic_search_mcp.tools import indexing

        assert provider_imports(inspect.getsource(indexing)) == []

    def test_the_manifest_imports_no_provider(self):
        import inspect

        assert provider_imports(inspect.getsource(manifest)) == []


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------


class TestTheAgent:
    def test_it_states_the_index_is_not_an_authorisation_boundary(self):
        joined = " ".join(SEMANTIC_SEARCH_AGENT.boundaries).lower()
        assert "authorisation boundary" in joined

    def test_it_states_that_filtered_is_not_missing(self):
        joined = " ".join(SEMANTIC_SEARCH_AGENT.boundaries).lower()
        assert "filtered-out" in joined

    def test_the_system_prompt_is_deterministic(self):
        assert system_prompt("searching") == system_prompt("searching")

    def test_the_system_prompt_carries_the_boundaries(self):
        prompt = system_prompt()
        for boundary in SEMANTIC_SEARCH_AGENT.boundaries:
            assert boundary in prompt

    def test_a_conditional_prompt_is_selected_when_it_applies(self):
        assert "three numbers" in system_prompt("searching")

    def test_a_conditional_prompt_is_absent_when_it_does_not(self):
        assert "three numbers" not in system_prompt("indexing")


# --------------------------------------------------------------------------
# The validator refuses what it should
# --------------------------------------------------------------------------


class TestTheValidatorRefuses:
    def test_an_unclassified_tool(self):
        from albanna_ai_chat.core.tools import ToolSpec

        mod = types.ModuleType("broken")
        mod.TOOLS = [
            ToolSpec(name="x", description="d", parameters={"type": "object", "properties": {}})
        ]
        mod.TIERS = {}
        with pytest.raises(ContractViolation):
            validate_module(mod, known_tools=set(CORE_TOOLS.names()))

    def test_a_collision_with_a_core_tool(self):
        from albanna_ai_chat.core.tools import ToolSpec

        mod = types.ModuleType("broken")
        mod.TOOLS = [
            ToolSpec(
                name="read_file",
                description="d",
                parameters={"type": "object", "properties": {}},
                tier=READ,
            )
        ]
        mod.TIERS = {"read_file": READ}
        with pytest.raises(ContractViolation):
            validate_module(mod, known_tools=set(CORE_TOOLS.names()))

    def test_a_sql_parameter(self):
        from albanna_ai_chat.core.tools import ToolSpec

        mod = types.ModuleType("broken")
        mod.TOOLS = [
            ToolSpec(
                name="run_sql",
                description="d",
                parameters={"type": "object", "properties": {"sql": {"type": "string"}}},
                tier=READ,
            )
        ]
        mod.TIERS = {"run_sql": READ}
        with pytest.raises(ContractViolation):
            validate_module(mod, known_tools=set(CORE_TOOLS.names()))
