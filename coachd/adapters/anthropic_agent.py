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

import base64
from typing import Awaitable, AsyncIterator, Callable, Iterable

from ..ports.llm import AgentResult, LLMError

# 1M context beta tag (verified literal accepted by ClaudeAgentOptions.betas).
CONTEXT_1M_BETA = "context-1m-2025-08-07"

# SDK AssistantMessage.error / ResultMessage error codes that should retry.
_RETRYABLE = {"rate_limit", "server_error"}

CanUseTool = Callable[[str, dict, object], Awaitable[object]]

_MODELS_URL = "https://api.anthropic.com/v1/models"


def _http_status(url: str, headers: dict) -> int:
    """GET ``url`` with ``headers``, return the HTTP status (HTTPError → its code)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def probe_anthropic_auth(
    api_key: str, oauth_token: str, *, status_fn: Callable[[str, dict], int] = _http_status
) -> str:
    """Validate the Anthropic credential at boot — fail fast with a clear message
    instead of a per-turn "error result: success" crash-loop on a bad key.

    Returns ``"valid"`` / ``"rejected"`` / ``"unreachable"``. The two credential
    kinds authenticate differently (verified live): a subscription OAuth token
    (``claude setup-token``) via ``Authorization: Bearer``; a pay-as-you-go API
    key via ``x-api-key``. A network failure is ``"unreachable"`` (tolerated — a
    transient outage must not block boot), NOT ``"rejected"`` (the Garmin EXPIRED
    vs UNREACHABLE lesson)."""
    if oauth_token:
        headers = {"Authorization": f"Bearer {oauth_token}", "anthropic-version": "2023-06-01"}
    elif api_key:
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        return "rejected"  # config guards this, but be explicit
    try:
        code = status_fn(_MODELS_URL, headers)
    except Exception:  # noqa: BLE001 — any network error is "can't tell", not "bad key"
        return "unreachable"
    if code == 200:
        return "valid"
    if code in (401, 403):
        return "rejected"
    return "unreachable"  # 5xx / unexpected — don't block boot over it


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


def image_user_message(prompt: str, image: tuple[bytes, str]) -> AsyncIterator[dict]:
    """An async stream yielding ONE user message: [text block, base64 image block].

    This is the SDK's iterable input branch (``client.query`` forwards each dict to
    the CLI verbatim — T0-verified that the CLI forwards the image to the model).
    The string branch (no image) auto-wraps a plain prompt; this branch is needed
    only when an image rides along. ``session_id`` is filled in by the SDK.
    """
    data, media_type = image
    b64 = base64.b64encode(data).decode("ascii")

    async def _gen() -> AsyncIterator[dict]:
        yield {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                ],
            },
            "parent_tool_use_id": None,
        }

    return _gen()


def sdk_allow() -> object:
    """Build the SDK's allow result (reads pass through)."""
    from claude_agent_sdk import PermissionResultAllow
    return PermissionResultAllow(behavior="allow", updated_input=None, updated_permissions=None)


def sdk_deny(message: str) -> object:
    """Build the SDK's deny result for a parked write.

    ``interrupt=False``: the write is already parked, so we let the turn finish
    normally — the model receives ``message`` as the denial reason, stops, and
    summarises the proposal for the user. ``interrupt=True`` would abort the turn
    with an error result (raising LLMError downstream and discarding both the
    model's explanation and — via the error branch — the parked action), so the
    confirm buttons never reached the user.
    """
    from claude_agent_sdk import PermissionResultDeny
    return PermissionResultDeny(behavior="deny", message=message, interrupt=False)


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
        client_cls: Callable[..., object] | None = None,
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
        self._client_cls = client_cls

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

    async def run_turn(
        self, prompt: str, *, image: tuple[bytes, str] | None = None
    ) -> AgentResult:
        options = self._build_options()
        # An image always rides the guarded streaming path (it's a chat-only input;
        # reports never send one). The guarded client keeps the channel open, which
        # the iterable image message needs — and the write-guard stays wired.
        if image is not None or self._can_use_tool is not None:
            return await self._run_turn_guarded(prompt, options, image=image)
        return await self._run_turn_oneshot(prompt, options)

    async def _run_turn_oneshot(self, prompt: str, options) -> AgentResult:
        """Read-only path (reports): a one-shot string query. No permission
        round-trip, so the SDK may close stdin right after the prompt — fine."""
        if self._query_fn is not None:
            query_fn = self._query_fn
        else:
            from claude_agent_sdk import query as query_fn
        messages = [msg async for msg in query_fn(prompt=prompt, options=options)]
        return extract_result(messages)

    async def _run_turn_guarded(
        self, prompt: str, options, *, image: tuple[bytes, str] | None = None
    ) -> AgentResult:
        """Write-guarded path (chat): the can_use_tool permission round-trip needs
        stdin open for the WHOLE turn. The one-shot ``query()`` closes stdin right
        after the prompt unless SDK-MCP servers / hooks are present (we have a
        stdio MCP and no hooks), so the CLI's permission request hits a closed
        stream ("Stream closed") and the guard never fires. ``ClaudeSDKClient``
        keeps the bidirectional channel open until the result — the supported way
        to use can_use_tool.

        When ``image`` is set, the query is the iterable [text, image] message
        instead of a plain string — same client, same guard, just a richer input."""
        if self._client_cls is not None:
            client_cls = self._client_cls
        else:
            from claude_agent_sdk import ClaudeSDKClient as client_cls
        query_input = prompt if image is None else image_user_message(prompt, image)
        messages: list[object] = []
        async with client_cls(options=options) as client:
            await client.query(query_input)
            async for msg in client.receive_response():
                messages.append(msg)
        return extract_result(messages)
