"""ToolProvider port — the single data-source seam (architecture decision #1).

The core never names Garmin. A provider contributes: an MCP server config, the
read/write tool allowlists (qualified ``mcp__<name>__<tool>``), and a
system-prompt fragment describing its data. Adding Apple/Whoop later = a new
class implementing this Protocol — no core change.

Thin on purpose: one Protocol, one implementation (GarminProvider). No registry
or entry-point discovery — that gets earned when a real second provider exists
(the abstraction must target the *fragile* axis, not the stable "which provider"
one). See the architecture doc's T1 decision.
"""

from __future__ import annotations

from typing import Protocol


class ToolProvider(Protocol):
    def name(self) -> str:
        """Short MCP server id, e.g. 'garmin'."""

    def mcp_servers(self) -> dict:
        """ClaudeAgentOptions.mcp_servers mapping for this provider."""

    def read_tools(self) -> list[str]:
        """Qualified read-only tool names (always safe to expose)."""

    def write_tools(self) -> list[str]:
        """Qualified write tool names — exposed ONLY in interactive flows and
        ALWAYS routed through the write-guard (can_use_tool). Never includes
        destructive operations."""

    def system_prompt_fragment(self) -> str:
        """Text describing this data source + tool-usage gotchas, injected into
        the system prompt."""
