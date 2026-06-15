"""Composition root — assemble the object graph from a ServiceConfig.

This is the one place that knows how all the pieces fit: config → GarminProvider
→ system prompt (methodology + provider fragment) → the report agent (read-only,
no guard) and the chat agent (read + write, write-guarded) → CoachEngine,
messenger, owner gate, pending store.

Everything below the config is pure construction — no I/O, no network, no CLI —
so build_app() runs in tests. The SDK/CLI are touched only when an agent turn
actually executes (and query_fn/options_cls can be injected to avoid even that).

State (journal, pending) lives in the data root (the parent of the token store),
i.e. the mounted /data volume, so it survives restarts alongside the tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Callable

from .adapters.anthropic_agent import AnthropicAgent, sdk_allow, sdk_deny
from .adapters.garmin_provider import GarminProvider
from .adapters.telegram import TelegramMessenger
from .config import ServiceConfig
from .core.engine import CoachEngine
from .core.journal import Journal
from .core.pending import PendingStore
from .core.prompts import build_system_prompt
from .security.authenticator import OwnerGate
from .security.write_guard import default_confirm_message, make_write_guard


def load_methodology() -> str:
    """Read the packaged methodology.md (ships in the wheel)."""
    return (files("coachd.prompts") / "methodology.md").read_text(encoding="utf-8")


@dataclass
class App:
    config: ServiceConfig
    provider: GarminProvider
    engine: CoachEngine
    chat_agent: AnthropicAgent
    messenger: TelegramMessenger
    owner_gate: OwnerGate
    pending: PendingStore


def build_app(
    config: ServiceConfig,
    *,
    methodology: str | None = None,
    query_fn: Callable[..., object] | None = None,
    options_cls: Callable[..., object] | None = None,
    post: Callable[[str, bytes], None] | None = None,
) -> App:
    data_root = Path(config.tokenstore).expanduser().parent

    provider = GarminProvider(config.tokenstore)
    system_prompt = build_system_prompt(
        methodology if methodology is not None else load_methodology(),
        provider.system_prompt_fragment(),
    )

    # --- report agent: read-only, no write tools, no guard needed ---
    report_agent = AnthropicAgent(
        model=config.model,
        system_prompt=system_prompt,
        mcp_servers=provider.mcp_servers(),
        allowed_tools=provider.read_tools(),
        use_1m_context=config.use_1m_context,
        query_fn=query_fn,
        options_cls=options_cls,
    )

    # --- chat agent: read + write tools, every write parked by the guard ---
    pending = PendingStore(data_root / "pending.json")
    write_guard = make_write_guard(
        pending,
        provider.write_tools(),
        allow=sdk_allow,
        deny=lambda action: sdk_deny(default_confirm_message(action)),
    )
    chat_agent = AnthropicAgent(
        model=config.model,
        system_prompt=system_prompt,
        mcp_servers=provider.mcp_servers(),
        allowed_tools=provider.read_tools() + provider.write_tools(),
        can_use_tool=write_guard,
        use_1m_context=config.use_1m_context,
        query_fn=query_fn,
        options_cls=options_cls,
    )

    engine = CoachEngine(
        llm=report_agent,
        journal=Journal(data_root / "journal.jsonl"),
        user_name=config.user_name,
        worn_start=config.worn_start,
    )

    if post is not None:
        messenger = TelegramMessenger(config.tg_bot_token, config.owner_chat_ids[0], post=post)
    else:
        messenger = TelegramMessenger(config.tg_bot_token, config.owner_chat_ids[0])

    owner_gate = OwnerGate(config.owner_chat_ids)

    return App(
        config=config,
        provider=provider,
        engine=engine,
        chat_agent=chat_agent,
        messenger=messenger,
        owner_gate=owner_gate,
        pending=pending,
    )
