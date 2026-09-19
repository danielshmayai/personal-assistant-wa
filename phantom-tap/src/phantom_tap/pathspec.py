"""A deliberately small JSON path resolver.

The protocol is discovered at runtime and described in `config/endpoints.json`,
so every field access is a string from a config file rather than an attribute in
code. That needs a path language, but not a big one: dotted keys, `[0]` for a
fixed index and `[*]` to fan out over a list is the whole grammar the discovered
Holmes Place responses need. Pulling in jsonpath-ng for that would buy syntax we
never use and lose the precise error messages that make a broken capture obvious.
"""

from __future__ import annotations

import re
from typing import Any

_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\*|-?\d+)\]")


class PathError(ValueError):
    """The path does not fit the document. Carries the prefix that still matched."""


def _tokenize(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    pos = 0
    for m in _SEGMENT.finditer(path):
        if m.start() > pos and path[pos : m.start()] not in (".", ""):
            raise PathError(f"unparsable path {path!r} at offset {pos}")
        key, index = m.group(1), m.group(2)
        tokens.append(key if key is not None else ("*" if index == "*" else int(index)))
        pos = m.end()
    if not tokens:
        raise PathError(f"empty path {path!r}")
    return tokens


def resolve(data: Any, path: str, default: Any = None) -> Any:
    """Read `path` out of `data`.

    A `[*]` anywhere makes the result a list. A miss returns `default` rather than
    raising, because a response that simply omits an optional field is normal and
    the caller decides whether that is fatal.
    """
    tokens = _tokenize(path)
    try:
        return _walk(data, tokens)
    except PathError:
        return default


def require(data: Any, path: str) -> Any:
    """Like `resolve`, but a miss is an error naming how far the path got.

    Used for fields whose absence means the protocol changed under us.
    """
    tokens = _tokenize(path)
    return _walk(data, tokens)


def _walk(node: Any, tokens: list[str | int]) -> Any:
    for i, token in enumerate(tokens):
        prefix = _render(tokens[:i])
        if token == "*":
            if not isinstance(node, list):
                raise PathError(f"{prefix or '<root>'} is {type(node).__name__}, not a list")
            rest = tokens[i + 1 :]
            out = []
            for item in node:
                try:
                    out.append(_walk(item, rest) if rest else item)
                except PathError:
                    continue  # a list of mixed shapes is normal; skip what does not fit
            return out
        if isinstance(token, int):
            if not isinstance(node, list):
                raise PathError(f"{prefix or '<root>'} is {type(node).__name__}, not a list")
            try:
                node = node[token]
            except IndexError:
                raise PathError(f"{prefix}[{token}] out of range (len {len(node)})") from None
        else:
            if not isinstance(node, dict):
                raise PathError(f"{prefix or '<root>'} is {type(node).__name__}, not an object")
            if token not in node:
                keys = ", ".join(sorted(node)[:8]) or "<empty>"
                raise PathError(f"{prefix or '<root>'} has no key {token!r}; has: {keys}")
            node = node[token]
    return node


def _render(tokens: list[str | int]) -> str:
    out = ""
    for t in tokens:
        out += f"[{t}]" if isinstance(t, int) or t == "*" else (f".{t}" if out else str(t))
    return out


def find_paths(data: Any, predicate: Any, _prefix: str = "") -> list[str]:
    """Every path whose leaf satisfies `predicate(key, value)`.

    This is the discovery direction: given a captured response, find where the
    token / the class list / the seat array actually live. Used by `pt analyze`.
    """
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{_prefix}.{key}" if _prefix else key
            if predicate(key, value):
                found.append(path)
            found.extend(find_paths(value, predicate, path))
    elif isinstance(data, list):
        for idx, value in enumerate(data[:1]):  # shape is uniform; one sample is enough
            path = f"{_prefix}[{idx}]"
            if predicate(None, value):
                found.append(path)
            found.extend(find_paths(value, predicate, path))
    return found
