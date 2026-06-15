"""Claude Agent SDK adapter — runs one agentic turn with MCP tools.

Implements :class:`coachd.ports.llm.LLMPort` over ``claude_agent_sdk.query``.
Verified against claude-agent-sdk 0.2.101:

  * ``can_use_tool(name, input, ctx) -> Awaitable[Allow|Deny]`` is the runtime
    permission chokepoint — the engine injects the write-guard here (#4).
  * ``mcp_servers`` takes stdio configs, so a ToolProvider's pinned MCP plugs in.
  * ``betas=['context-1m-2025-08-07']`` enables the 1M context window.
  * ``ResultMessage.total_cost_usd`` is what the cache spike (#2.5) measures.

NOTE: the SDK spawns the ``claude`` CLI binary (Node) — ``CLINotFoundError`` /
``cli_path`` confirm this. The runtime image must ship the CLI; this adapter only
needs it when ``run_turn`` actually executes (not at import or for unit tests,
which inject a fake ``query``).

Message extraction is a pure function (:func:`extract_result`) so it is tested
with lightweight fakes, no CLI and no network.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Iterable

from ..ports.llm import AgentResult, LLMError

# 1M context beta tag (verified literal accepted by ClaudeAgentOptions.betas).
CONTEXT_1M_BETA = "context-1m-2025-08-07"

# SDK AssistantMessage.error / ResultMessage error codes that should retry.
_RETRYABLE = {"rate_limit", "server_error"}

CanUseTool = Callable[[str, dict, object], Awaitable[object]]


def extract_result(messages: Iterable[object]) -> AgentResult:
    """Reduce a stream of SDK messages to an :class:`AgentResult`.

    Duck-typed (not isinstance) so tests can pass simple stand-ins:
      * a result message uniquely has a ``total_cost_usd`` attribute;
      * an assistant message has a ``content`` list of blocks, and a text block
        has a ``text`` str;
      * an ``error`` code on either raises :class:`LLMError` (with the code).
    """
    text_parts: list[str] = []
    final_text: str | None = None
    cost: float | None = None
    usage: dict | None = None
    error_code: str | None = None

    for msg in messages:
        if hasattr(msg, "total_cost_usd"):  # ResultMessage
            final_text = getattr(msg, "result", None)
            cost = getattr(msg, "total_cost_usd", None)
            usage = getattr(msg, "usage", None)
            # classify the specific code BEFORE the generic is_error fallback,
            # so a 429 reads as retryable rate_limit, not opaque "unknown".
            if getattr(msg, "api_error_status", None) == 429:
                error_code = error_code or "rate_limit"
            if getattr(msg, "is_error", False):
                error_code = error_code or "unknown"
            continue

        content = getattr(msg, "content", None)
        if isinstance(content, list):  # AssistantMessage
            err = getattr(msg, "error", None)
            if err:
                error_code = error_code or str(err)
            for block in content:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    text_parts.append(text)

    if error_code:
        raise LLMError(f"agent turn failed: {error_code}", code=error_code)

    text = (final_text if final_text else "\n".join(text_parts)).strip()
    return AgentResult(text=text, cost_usd=cost, usage=usage)


def sdk_allow() -> object:
    """Build the SDK's allow result (reads pass through)."""
    from claude_agent_sdk import PermissionResultAllow
    return PermissionResultAllow(behavior="allow", updated_input=None, updated_permissions=None)


def sdk_deny(message: str) -> object:
    """Build the SDK's deny result for a parked write.

    ``interrupt=True`` stops the turn so the agent does not keep retrying the
    denied write — the action is parked and the user confirms out-of-band.
    """
    from claude_agent_sdk import PermissionResultDeny
    return PermissionResultDeny(behavior="deny", message=message, interrupt=True)


class AnthropicAgent:
    """LLMPort over the Claude Agent SDK.

    ``query_fn`` is injected (defaults to the real SDK) so unit tests drive the
    adapter with a fake async stream — no CLI, no network, no API key.
    """

    def __init__(
        self,
        *,
        model: str,
        system_prompt: str,
        mcp_servers: dict,
        allowed_tools: list[str],
        can_use_tool: CanUseTool | None = None,
        max_budget_usd: float | None = None,
        use_1m_context: bool = False,
        cli_path: str | None = None,
        query_fn: Callable[..., object] | None = None,
        options_cls: Callable[..., object] | None = None,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._mcp_servers = mcp_servers
        self._allowed_tools = allowed_tools
        self._can_use_tool = can_use_tool
        self._max_budget_usd = max_budget_usd
        self._use_1m = use_1m_context
        self._cli_path = cli_path
        # Imported lazily so unit tests need neither the SDK package internals
        # resolved at import time nor the claude CLI present.
        self._query_fn = query_fn
        self._options_cls = options_cls

    def _build_options(self):
        if self._options_cls is not None:
            options_cls = self._options_cls
        else:
            from claude_agent_sdk import ClaudeAgentOptions as options_cls
        return options_cls(
            model=self._model,
            system_prompt=self._system_prompt,
            mcp_servers=self._mcp_servers,
            allowed_tools=self._allowed_tools,
            can_use_tool=self._can_use_tool,
            permission_mode="default",
            betas=[CONTEXT_1M_BETA] if self._use_1m else [],
            max_budget_usd=self._max_budget_usd,
            cli_path=self._cli_path,
        )

    async def run_turn(self, prompt: str) -> AgentResult:
        if self._query_fn is not None:
            query_fn = self._query_fn
        else:
            from claude_agent_sdk import query as query_fn
        options = self._build_options()
        messages: list[object] = []
        # The SDK rejects a plain-string prompt whenever a can_use_tool callback
        # is set ("requires streaming mode"): the permission hook needs the
        # bidirectional channel that only the AsyncIterable input opens. Wrap the
        # single turn into a one-message stream in that case. The read-only report
        # path has no guard, so it keeps the simpler string mode.
        prompt_arg = _as_stream(prompt) if self._can_use_tool is not None else prompt
        async for msg in query_fn(prompt=prompt_arg, options=options):
            messages.append(msg)
        return extract_result(messages)


async def _as_stream(text: str):
    """One-shot streaming-mode input: a single user message, shaped as the SDK
    expects (see claude_agent_sdk.query streaming example)."""
    yield {
        "type": "user",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
        "session_id": "default",
    }
