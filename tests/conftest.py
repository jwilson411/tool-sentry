"""Shared fixture helpers. Everything here is offline and file-based."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"

# Allow `python3 -m pytest -q` from a clean checkout, before `pip install -e .`.
sys.path.insert(0, str(ROOT / "src"))


def load_fixture(name: str) -> Any:
    """Read a JSON fixture by file name."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def reverse_keys(value: Any) -> Any:
    """Rebuild ``value`` with every object's keys in reverse order.

    JSON objects are unordered, so this is an equivalent document.
    """
    if isinstance(value, dict):
        return {key: reverse_keys(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [reverse_keys(item) for item in value]
    return value


def mangle_whitespace(value: Any, *, key: str | None = None) -> Any:
    """Pad description strings with newlines, tabs, and doubled spaces."""
    if isinstance(value, dict):
        return {k: mangle_whitespace(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [mangle_whitespace(item) for item in value]
    if key in {"description", "title"} and isinstance(value, str):
        return "\n  " + value.replace(" ", "  \t ") + "\n\n"
    return value


def clone(value: Any) -> Any:
    """Deep copy so tests never mutate a loaded fixture."""
    return copy.deepcopy(value)


@pytest.fixture
def openai_doc() -> Any:
    return load_fixture("openai_tools.json")


@pytest.fixture
def anthropic_doc() -> Any:
    return load_fixture("anthropic_tools.json")


@pytest.fixture
def mcp_doc() -> Any:
    return load_fixture("mcp_tools_list.json")
