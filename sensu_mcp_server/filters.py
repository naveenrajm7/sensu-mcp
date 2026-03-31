"""Client-side implementation of Sensu API response filtering.

Replicates the commercial Sensu Go fieldSelector / labelSelector API so that
open-source Sensu deployments can still filter results.

Supported operators:  ==  !=  in  notin  matches  &&

Usage::

    from sensu_mcp_server.filters import SensuFilter

    f = SensuFilter(
        field_selector="entity.entity_class == agent && linux in entity.subscriptions",
        label_selector="region == us-east-1",
    )
    filtered = f.apply(raw_entity_list)

Reference: https://docs.sensu.io/sensu-go/latest/api/other/apiconfig/
"""

from __future__ import annotations

import re
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Field resolvers
# Maps every Sensu field-selector path documented in the API reference to a
# callable that extracts the corresponding value from the resource dict.
# ---------------------------------------------------------------------------

def _get(d: Any, *keys: str, default: Any = None) -> Any:
    """Safe nested-key access; returns *default* if any key is missing."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


_FIELD_RESOLVERS: dict[str, Callable[[dict], Any]] = {
    # --- Entity ---
    "entity.name":          lambda r: _get(r, "metadata", "name"),
    "entity.namespace":     lambda r: _get(r, "metadata", "namespace"),
    "entity.deregister":    lambda r: r.get("deregister"),
    "entity.entity_class":  lambda r: r.get("entity_class"),
    "entity.subscriptions": lambda r: r.get("subscriptions") or [],

    # --- Check ---
    "check.name":           lambda r: _get(r, "metadata", "name"),
    "check.namespace":      lambda r: _get(r, "metadata", "namespace"),
    "check.handlers":       lambda r: r.get("handlers") or [],
    "check.publish":        lambda r: r.get("publish"),
    "check.round_robin":    lambda r: r.get("round_robin"),
    "check.runtime_assets": lambda r: r.get("runtime_assets") or [],
    "check.subscriptions":  lambda r: r.get("subscriptions") or [],

    # --- Event ---
    "event.is_silenced":          lambda r: r.get("is_silenced"),
    "event.check.handlers":       lambda r: _get(r, "check", "handlers") or [],
    "event.check.is_silenced":    lambda r: _get(r, "check", "is_silenced"),
    "event.check.name":           lambda r: _get(r, "check", "metadata", "name"),
    "event.check.publish":        lambda r: _get(r, "check", "publish"),
    "event.check.round_robin":    lambda r: _get(r, "check", "round_robin"),
    "event.check.runtime_assets": lambda r: _get(r, "check", "runtime_assets") or [],
    "event.check.state":          lambda r: _get(r, "check", "state"),
    "event.check.status":         lambda r: _get(r, "check", "status"),
    "event.check.subscriptions":  lambda r: _get(r, "check", "subscriptions") or [],
    "event.entity.deregister":    lambda r: _get(r, "entity", "deregister"),
    "event.entity.entity_class":  lambda r: _get(r, "entity", "entity_class"),
    "event.entity.name":          lambda r: _get(r, "entity", "metadata", "name"),
    "event.entity.subscriptions": lambda r: _get(r, "entity", "subscriptions") or [],
}


def _resolve_field(resource: dict, path: str) -> Any:
    resolver = _FIELD_RESOLVERS.get(path)
    return resolver(resource) if resolver else None


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

def _parse_value(s: str) -> Any:
    """Parse a raw value token from a filter expression.

    Handles: quoted strings, arrays ([a,b,c]), booleans, integers, plain strings.
    """
    s = s.strip()
    # Quoted string — strip outer quotes
    if len(s) >= 2 and s[0] in ('"', "'") and s[-1] == s[0]:
        return s[1:-1]
    # Array literal [a, b, c]
    if s.startswith("[") and s.endswith("]"):
        return [v.strip().strip('"').strip("'") for v in s[1:-1].split(",") if v.strip()]
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    return s


def _to_str(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    return str(v)


# ---------------------------------------------------------------------------
# Clause — a single filter condition
# ---------------------------------------------------------------------------

# Operator detection regexes (ordered: most specific first to avoid mismatch)
_RE_MATCHES = re.compile(r"^(.+?)\s+matches\s+(.+)$",  re.IGNORECASE)
_RE_NOTIN   = re.compile(r"^(.+?)\s+notin\s+(.+)$",    re.IGNORECASE)
_RE_IN      = re.compile(r"^(.+?)\s+in\s+(.+)$",       re.IGNORECASE)
_RE_NEQ     = re.compile(r"^(.+?)\s*!=\s*(.+)$")
_RE_EQ      = re.compile(r"^(.+?)\s*==\s*(.+)$")


class _Clause:
    """One parsed filter condition."""

    __slots__ = ("op", "lhs_raw", "rhs")

    def __init__(self, op: str, lhs_raw: str, rhs: Any) -> None:
        self.op = op
        # lhs_raw is a field path (entity.name) or a literal value,
        # depending on which form of `in`/`notin` was used.
        self.lhs_raw = lhs_raw
        self.rhs = rhs

    # --- field-selector evaluation ---

    def field_matches(self, resource: dict) -> bool:
        op = self.op

        if op in ("in", "notin"):
            if isinstance(self.rhs, list):
                # Form: field in [v1, v2, ...]  — lhs is a field path
                field_val = _resolve_field(resource, self.lhs_raw)
                result = _to_str(field_val) in [_to_str(v) for v in self.rhs]
            else:
                # Form: value in field  — lhs is a literal, rhs is the field path
                field_val = _resolve_field(resource, str(self.rhs))
                lhs_str = _to_str(self.lhs_raw)
                if isinstance(field_val, list):
                    result = lhs_str in [_to_str(v) for v in field_val]
                else:
                    result = lhs_str == _to_str(field_val)
            return result if op == "in" else not result

        # For ==, !=, matches — lhs is always the field path
        field_val = _resolve_field(resource, self.lhs_raw)

        if op == "==":
            return _to_str(field_val) == _to_str(self.rhs)
        if op == "!=":
            return _to_str(field_val) != _to_str(self.rhs)
        if op == "matches":
            return _to_str(self.rhs) in _to_str(field_val)

        return True

    # --- label-selector evaluation ---

    def label_matches(self, labels: dict[str, str]) -> bool:
        op = self.op

        if op in ("in", "notin"):
            if isinstance(self.rhs, list):
                # Form: labelkey in [v1, v2, ...]
                label_val = labels.get(self.lhs_raw, "")
                result = label_val in [_to_str(v) for v in self.rhs]
            else:
                # Form: value in labelkey
                label_val = labels.get(str(self.rhs), "")
                result = _to_str(self.lhs_raw) in label_val
            return result if op == "in" else not result

        label_val = labels.get(self.lhs_raw, "")

        if op == "==":
            return label_val == _to_str(self.rhs)
        if op == "!=":
            return label_val != _to_str(self.rhs)
        if op == "matches":
            return _to_str(self.rhs) in label_val

        return True


def _parse_clause(expr: str) -> _Clause:
    """Parse a single operator clause from an expression string."""
    expr = expr.strip()

    m = _RE_MATCHES.match(expr)
    if m:
        return _Clause("matches", m.group(1).strip(), _parse_value(m.group(2)))

    m = _RE_NOTIN.match(expr)
    if m:
        lhs_raw = m.group(1).strip().strip('"').strip("'")
        rhs = _parse_value(m.group(2).strip())
        return _Clause("notin", lhs_raw, rhs)

    m = _RE_IN.match(expr)
    if m:
        lhs_raw = m.group(1).strip().strip('"').strip("'")
        rhs = _parse_value(m.group(2).strip())
        return _Clause("in", lhs_raw, rhs)

    m = _RE_NEQ.match(expr)
    if m:
        return _Clause("!=", m.group(1).strip(), _parse_value(m.group(2)))

    m = _RE_EQ.match(expr)
    if m:
        return _Clause("==", m.group(1).strip(), _parse_value(m.group(2)))

    raise ValueError(f"Cannot parse filter clause: {expr!r}")


# ---------------------------------------------------------------------------
# Expression splitter — respects quotes and brackets when splitting on &&
# ---------------------------------------------------------------------------

def _split_and(expr: str) -> list[str]:
    """Split a filter expression on && (logical AND).

    Does not split inside quoted strings or bracketed arrays.
    """
    clauses: list[str] = []
    depth = 0
    in_quote: str | None = None
    buf: list[str] = []
    i = 0
    while i < len(expr):
        c = expr[i]
        if c in ('"', "'") and in_quote is None:
            in_quote = c
            buf.append(c)
        elif c == in_quote:
            in_quote = None
            buf.append(c)
        elif in_quote is None and c == "[":
            depth += 1
            buf.append(c)
        elif in_quote is None and c == "]":
            depth -= 1
            buf.append(c)
        elif in_quote is None and depth == 0 and expr[i : i + 2] == "&&":
            clauses.append("".join(buf).strip())
            buf = []
            i += 2
            continue
        else:
            buf.append(c)
        i += 1
    if buf:
        clauses.append("".join(buf).strip())
    return [c for c in clauses if c]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SensuFilter:
    """Client-side Sensu response filter.

    Accepts the same ``fieldSelector`` and ``labelSelector`` syntax as the
    commercial Sensu API and evaluates them locally against resource dicts.

    Supported operators: ``==``  ``!=``  ``in``  ``notin``  ``matches``  ``&&``

    Examples::

        # Single condition
        SensuFilter(field_selector="entity.entity_class == agent")

        # Multiple conditions joined with AND
        SensuFilter(field_selector="entity.entity_class == agent && linux in entity.subscriptions")

        # Label filtering
        SensuFilter(label_selector="region == us-east-1")

        # Combined field + label selectors
        SensuFilter(
            field_selector="linux in check.subscriptions",
            label_selector='type != "server"',
        )

        # Substring match
        SensuFilter(field_selector='entity.name matches "webserver-"')

        # Array membership (field in list)
        SensuFilter(field_selector="check.namespace in [dev,production]")

        # Event severity filter
        SensuFilter(field_selector='event.check.status == "2"')
    """

    def __init__(
        self,
        field_selector: str | None = None,
        label_selector: str | None = None,
    ) -> None:
        self._field_clauses: list[_Clause] = []
        self._label_clauses: list[_Clause] = []

        if field_selector and field_selector.strip():
            for clause_str in _split_and(field_selector):
                self._field_clauses.append(_parse_clause(clause_str))

        if label_selector and label_selector.strip():
            for clause_str in _split_and(label_selector):
                self._label_clauses.append(_parse_clause(clause_str))

    @property
    def is_empty(self) -> bool:
        return not self._field_clauses and not self._label_clauses

    def matches(self, resource: dict) -> bool:
        """Return True if *resource* satisfies all clauses (AND semantics)."""
        for clause in self._field_clauses:
            if not clause.field_matches(resource):
                return False
        if self._label_clauses:
            labels: dict[str, str] = (resource.get("metadata") or {}).get("labels") or {}
            for clause in self._label_clauses:
                if not clause.label_matches(labels):
                    return False
        return True

    def apply(self, resources: list[dict]) -> list[dict]:
        """Return the subset of *resources* that match all clauses."""
        if self.is_empty:
            return resources
        return [r for r in resources if self.matches(r)]
