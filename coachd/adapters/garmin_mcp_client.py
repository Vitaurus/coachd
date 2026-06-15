"""Direct MCP tool execution for confirmed write-actions.

When the user confirms a parked workout, the write must run exactly as proposed —
no LLM in the loop, so it is deterministic and cannot drift. This calls the
Garmin MCP tool directly over stdio with the stored payload, using the SAME MCP
config the agent uses (so the same pinned server + token store).

``bare_tool`` and ``extract_tool_text`` are pure (tested). ``execute`` spawns the
MCP subprocess and is exercised at runtime.
"""

from __future__ import annotations

from ..core.pending import PendingAction


def bare_tool(qualified: str) -> str:
    """'mcp__garmin__upload_workout' -> 'upload_workout' (the MCP's own tool name)."""
    return qualified.rsplit("__", 1)[-1]


def extract_tool_text(result: object) -> str:
    """Join the text content blocks of an MCP CallToolResult."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts).strip() or "ok"


class GarminMcpExecutor:
    """Executes a confirmed PendingAction by calling its tool on the Garmin MCP."""

    def __init__(self, mcp_config: dict) -> None:
        # mcp_config is provider.mcp_servers()["garmin"]: {command, args, env}
        self._cfg = mcp_config

    async def execute(self, action: PendingAction) -> str:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self._cfg["command"],
            args=list(self._cfg.get("args", [])),
            env=self._cfg.get("env"),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(bare_tool(action.tool), action.input)
                return extract_tool_text(result)
