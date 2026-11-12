"""Frappe app hooks — the one file Frappe reads to wire this app in.

This app is an *extension* to the frappe-mcp core.  It contributes tools, an
agent and prompts, and it is installed and uninstalled like any other Frappe
app — which is the whole point of using a Frappe hook rather than a Python
entry point.

The one line that matters
-------------------------
``frappe_mcp_tools`` points at this app's manifest module.  The core resolves
that hook **per site**, validates it atomically, and absorbs the tools tagged
with this app's name.  Install this app and the chat gains a search-by-meaning
agent; uninstall it and those tools are gone, and only those tools.

Why not a Python entry point
----------------------------
An entry point lives in the bench *virtualenv*, which every site on the bench
shares.  Install this extension there and its tools appear on every site
running the core — including sites that do not have this app installed, where
``Indexed DocType`` does not exist.  A Frappe hook is site-scoped, and the
scoping comes for free.

The document hooks, and why every one of them only enqueues
-----------------------------------------------------------
``after_insert``, ``on_update`` and ``on_trash`` are wired here, and each one
queues work and returns.  A hook that called an embedding provider inline would
make a third-party API a dependency of every save on the site: a slow provider
makes every write slow, and a down provider makes every write **fail**, because
a hook that raises rolls back the transaction.  The core's validator refuses an
extension whose source reaches past it for a provider, so there is no provider
here to call.
"""

app_name = "semantic_search_mcp"
app_title = "Semantic Search MCP"
app_publisher = "Albanna Software Solutions"
app_description = (
    "Find records by meaning — the reference extension for queued embedding, "
    "permission-filtered retrieval and a complete index lifecycle."
)
app_email = "ahmedalbanna966@gmail.com"
app_license = "mit"


# --------------------------------------------------------------------------
# The extension seam.
#
# The value is a dotted path to a module carrying TOOLS and TIERS.  The core
# imports it, validates it atomically, and absorbs it.
# --------------------------------------------------------------------------

frappe_mcp_tools = "semantic_search_mcp.tools.manifest"

# --------------------------------------------------------------------------
# The agent this extension brings.
#
# The core's prompt assembler reads this to build the system prompt when this
# extension's agent is the active one.  Pure data, so it is testable without
# a site.
# --------------------------------------------------------------------------

frappe_mcp_agent = "semantic_search_mcp.agent"

# The installer creates the Single settings row and the role, so the extension
# is usable and configurable the moment it is installed.
after_install = "semantic_search_mcp.install.after_install"
after_migrate = "semantic_search_mcp.install.after_migrate"

# --------------------------------------------------------------------------
# The document lifecycle.
#
# Every one of these enqueues and returns.  See the module docstring for why
# an inline provider call here would be a site-wide outage with a friendly
# name.
#
# ``doc_events`` is keyed by DocType, and an operator adds the DocTypes they
# want indexed.  Nothing is indexed until they do — an extension that indexed
# every doctype on the site by default would be an extension that made a
# decision nobody authorised.
# --------------------------------------------------------------------------

doc_events = {}
