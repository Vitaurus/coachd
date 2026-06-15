"""Pin the owner gate — the only thing between a stranger and the owner's data."""

from __future__ import annotations

import pytest

from coachd.security.authenticator import OwnerGate


def test_allows_owner_denies_others():
    g = OwnerGate([12345])
    assert g.allows(12345) is True
    assert g.allows(99999) is False


def test_string_chat_id_coerced():
    g = OwnerGate([12345])
    assert g.allows("12345") is True


def test_household_allowlist():
    g = OwnerGate([1, 2, 3])
    assert g.allows(2) is True
    assert g.allows(4) is False


def test_garbage_chat_id_denied():
    g = OwnerGate([1])
    assert g.allows(None) is False
    assert g.allows("not-an-int") is False
    assert g.allows({}) is False


def test_empty_allowlist_rejected():
    with pytest.raises(ValueError):
        OwnerGate([])
