"""The agent this extension brings, and the prompts it brings with it.

An extension is not only tools.  Every repo in this portfolio carries its own
tools, permissions, configurations, *agent* and *prompts* — and the agent is
what makes a set of tools into something with a point of view.  A chat with six
search tools and no agent is a chat that will report a filtered-out result as a
missing one.

This module is pure data.  It is Frappe-free so it can be tested without a
site, and so the core can assemble a system prompt from it without importing
anything this extension depends on.

How the core uses it
--------------------
The core's prompt assembler is deterministic and ordered: the core's own system
prompt, then the *active* extension's agent persona, then its task prompts,
then the conversation.  One active agent per conversation, chosen in Settings
and switchable mid-thread with the switch recorded.  Twenty extensions
contribute *content*; none of them contributes assembly logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: This extension's name, as the core knows it.  It is the app name, because
#: that is what provenance is tagged with and what an uninstall keys on.
EXTENSION_NAME = "semantic_search_mcp"


@dataclass(frozen=True)
class Prompt:
    """One instruction the agent is given.

    ``when`` says when it applies, so the assembler can include the prompts
    that matter for this turn rather than all of them every time — a prompt
    nobody needs is a prompt that dilutes the ones that do.
    """

    name: str
    text: str
    when: str = ""


@dataclass(frozen=True)
class Agent:
    """What this extension contributes to a conversation's point of view."""

    name: str
    persona: str
    boundaries: tuple[str, ...] = ()
    prompts: tuple[Prompt, ...] = field(default_factory=tuple)

    def always_on(self) -> tuple[Prompt, ...]:
        return tuple(p for p in self.prompts if not p.when)

    def for_situation(self, situation: str) -> tuple[Prompt, ...]:
        return tuple(p for p in self.prompts if p.when == situation)


SEMANTIC_SEARCH_AGENT = Agent(
    name="semantic_search",
    persona=(
        "You find records by what they mean, not by what they spell. You are "
        "precise about the difference between 'not found' and 'not permitted': "
        "a record the caller may not read is not a record that does not exist, "
        "and saying so is the difference between a useful answer and a "
        "misleading one."
    ),
    boundaries=(
        "Never present a filtered-out result as a missing one. Say how many "
        "were found and how many were removed by permissions.",
        "Never treat the index as an authorisation boundary. It is a recall "
        "optimisation; Frappe decides what may be returned.",
        "Never embed a field the operator has not configured. An index nobody "
        "chose is an index nobody audited.",
        "Never embed inside a document hook. Indexing is queued work, because "
        "a provider call in a hook makes a third-party API a dependency of "
        "every save on the site.",
    ),
    prompts=(
        Prompt(
            name="searching",
            text=(
                "When you report a search, give three numbers: how many "
                "candidates the index returned, how many the caller was "
                "permitted to see, and how many you are showing. A search that "
                "returned 40 candidates and 3 visible records is a different "
                "answer from one that returned 3."
            ),
            when="searching",
        ),
        Prompt(
            name="indexing",
            text=(
                "Indexing is queued work. Say that the records are being "
                "embedded in the background and that the index will be "
                "searchable when it finishes — never imply the search works "
                "already."
            ),
            when="indexing",
        ),
        Prompt(
            name="drift",
            text=(
                "A drifted index is a silent wrong answer. When index_drift "
                "reports missing or orphaned entries, say so plainly and offer "
                "to re-index, rather than letting a thin result stand."
            ),
            when="investigating_drift",
        ),
    ),
)


def system_prompt(situation: str = "") -> str:
    """The system prompt for this agent, deterministically assembled.

    Ordered: persona, then boundaries, then the always-on prompts, then the
    prompt for *situation* when there is one.  The same inputs give the same
    string, every time — a prompt that varied between calls would make a
    search impossible to reproduce.
    """
    parts = [SEMANTIC_SEARCH_AGENT.persona]

    if SEMANTIC_SEARCH_AGENT.boundaries:
        parts.append("You will not:")
        parts.extend(f"- {b}" for b in SEMANTIC_SEARCH_AGENT.boundaries)

    for prompt in SEMANTIC_SEARCH_AGENT.always_on():
        parts.append(prompt.text)

    if situation:
        for prompt in SEMANTIC_SEARCH_AGENT.for_situation(situation):
            parts.append(prompt.text)

    return "\n\n".join(parts)
