"""Deterministic structural classification of tool contract changes.

:func:`classify` compares a baseline roster of :class:`~tool_sentry.model.Tool`
against a candidate roster and returns a list of :class:`Change` records, each
labelled ``breaking``, ``risky``, or ``informational``.

The rules are structural and fixed. Nothing is executed, no model is asked,
and no attempt is made at full JSON Schema semantic equivalence: ``$ref`` is
not resolved, and ``unevaluatedProperties`` and friends are compared as plain
values. See the rule table in the README.

Severities
----------
``breaking``
    A call that was valid against the baseline can now fail, or a consumer
    that read the old output can now break.
``risky``
    A real contract delta that is not obviously safe and not obviously
    breaking. This is the default bucket for schema changes.
``informational``
    Documentation and additive changes that leave every previously valid
    call valid.

Matching
--------
Tools are matched by name, not by roster position, so reordering the roster
is informational. Ordering of the returned changes is stable: severity rank
(breaking, risky, informational), then tool name, then rule id, then path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .canonicalize import canonical_json, canonicalize_tools
from .model import Tool

Severity = Literal["breaking", "risky", "informational"]

#: Severities ordered from least to most severe. ``--fail-on`` compares here.
SEVERITY_ORDER: tuple[Severity, ...] = ("informational", "risky", "breaking")

#: Rank used by ``--fail-on``: informational < risky < breaking.
SEVERITY_RANK: dict[str, int] = {name: rank for rank, name in enumerate(SEVERITY_ORDER)}

#: Rank used for output ordering: most severe first.
_REPORT_RANK: dict[str, int] = {
    name: rank for rank, name in enumerate(reversed(SEVERITY_ORDER))
}

#: Constraints that get stricter as the value goes up.
MIN_CONSTRAINTS = ("exclusiveMinimum", "minItems", "minLength", "minimum")

#: Constraints that get stricter as the value goes down.
MAX_CONSTRAINTS = ("exclusiveMaximum", "maxItems", "maxLength", "maximum")

#: Schema keywords with a dedicated rule. Everything else falls through to
#: the generic ``schema.changed`` bucket.
HANDLED_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "default",
        "description",
        "enum",
        "example",
        "examples",
        "format",
        "items",
        "oneOf",
        "prefixItems",
        "properties",
        "required",
        "title",
        "type",
    }
    | set(MIN_CONSTRAINTS)
    | set(MAX_CONSTRAINTS)
)

UNION_KEYWORDS = ("allOf", "anyOf", "oneOf")
TEXT_KEYWORDS = ("description", "title")
EXAMPLE_KEYWORDS = ("example", "examples")


@dataclass(frozen=True)
class Change:
    """One classified difference between two contracts."""

    severity: Severity
    rule: str
    tool: str
    path: str
    message: str
    before: Any = None
    after: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON mapping, with keys in report order."""
        return {
            "severity": self.severity,
            "rule": self.rule,
            "tool": self.tool,
            "path": self.path,
            "message": self.message,
            "before": self.before,
            "after": self.after,
        }

    @property
    def sort_key(self) -> tuple[int, str, str, str]:
        """Stable ordering: severity, then tool, then rule, then path."""
        return (_REPORT_RANK[self.severity], self.tool, self.rule, self.path)


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_set(value: Any) -> set[str] | None:
    """Return ``type`` as a set of type names, or None if it is not one."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return set(value)
    return None


def _still_accepted(name: str, types: set[str]) -> bool:
    """True when dropping type ``name`` is a widening, not a narrowing.

    Every integer is a number, so ``integer -> number`` keeps old calls
    valid. The reverse does not.
    """
    return name == "integer" and "number" in types


def _delta_word(before: Any, after: Any) -> str:
    if before is None:
        return "added"
    if after is None:
        return "removed"
    return "changed"


def _same(before: Any, after: Any) -> bool:
    return canonical_json(before) == canonical_json(after)


def _allowed_values(node: dict[str, Any]) -> tuple[list[Any] | None, str | None]:
    """Return the literal values a node allows, and the keyword used."""
    enum = node.get("enum")
    if isinstance(enum, list):
        return enum, "enum"
    if "const" in node:
        return [node["const"]], "const"
    return None, None


def _value_set(values: list[Any]) -> set[str]:
    return {canonical_json(value) for value in values}


def _by_name(tools: list[Tool]) -> dict[str, Tool]:
    """Index tools by name, keeping the first tool of a repeated name."""
    indexed: dict[str, Tool] = {}
    for tool in tools:
        indexed.setdefault(tool.name, tool)
    return indexed


class _Classifier:
    """Collects changes while walking a pair of contracts."""

    def __init__(self) -> None:
        self.changes: list[Change] = []

    def add(
        self,
        severity: Severity,
        rule: str,
        tool: str,
        path: str,
        message: str,
        before: Any = None,
        after: Any = None,
    ) -> None:
        self.changes.append(Change(severity, rule, tool, path, message, before, after))

    # --- roster ------------------------------------------------------------

    def compare_rosters(self, baseline: list[Tool], candidate: list[Tool]) -> None:
        before = _by_name(baseline)
        after = _by_name(candidate)
        for name, tool in before.items():
            if name not in after:
                self.add(
                    "breaking",
                    "tool.removed",
                    name,
                    "",
                    f"tool {name!r} was removed",
                    name,
                    None,
                )
        for name in after:
            if name not in before:
                self.add(
                    "informational",
                    "tool.added",
                    name,
                    "",
                    f"tool {name!r} was added",
                    None,
                    name,
                )
        kept_before = [name for name in before if name in after]
        kept_after = [name for name in after if name in before]
        if kept_before != kept_after:
            self.add(
                "informational",
                "roster.reordered",
                "",
                "",
                "tool roster order changed",
                kept_before,
                kept_after,
            )
        for name in kept_before:
            self.compare_tool(before[name], after[name])

    def compare_tool(self, before: Tool, after: Tool) -> None:
        name = before.name
        if before.description != after.description:
            self.add(
                "informational",
                "description.changed",
                name,
                "description",
                f"tool description {_delta_word(before.description or None, after.description or None)}",
                before.description,
                after.description,
            )
        self.walk(before.parameters, after.parameters, "parameters", name, False)
        self.compare_output(before, after)

    def compare_output(self, before: Tool, after: Tool) -> None:
        name = before.name
        if before.output is None and after.output is None:
            return
        if after.output is None:
            self.add(
                "breaking",
                "output.field.removed",
                name,
                "output",
                "output schema was removed",
                before.output,
                None,
            )
            return
        if before.output is None:
            self.add(
                "informational",
                "output.field.optional.added",
                name,
                "output",
                "output schema was added",
                None,
                after.output,
            )
            return
        self.walk(before.output, after.output, "output", name, True)

    # --- schema walk -------------------------------------------------------

    def walk(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        path: str,
        tool: str,
        in_output: bool,
    ) -> None:
        """Compare two schema nodes. One primary classification per path."""
        self._text(before, after, path, tool)
        self._examples(before, after, path, tool)
        self._type(before, after, path, tool, in_output)
        self._allowed(before, after, path, tool)
        self._constraints(before, after, path, tool)
        self._keyword(before, after, path, tool, "format", "format.changed")
        self._keyword(before, after, path, tool, "default", "default.changed")
        self._additional_properties(before, after, path, tool, in_output)
        self._object(before, after, path, tool, in_output)
        self._arrays(before, after, path, tool, in_output)
        self._unions(before, after, path, tool, in_output)
        self._rest(before, after, path, tool)

    def _subschema(
        self,
        before: Any,
        after: Any,
        path: str,
        tool: str,
        in_output: bool,
        message: str,
    ) -> None:
        """Recurse into a nested schema, or report it as a plain value."""
        if isinstance(before, dict) and isinstance(after, dict):
            self.walk(before, after, path, tool, in_output)
        elif not _same(before, after):
            self.add("risky", "schema.changed", tool, path, message, before, after)

    def _text(self, before: dict, after: dict, path: str, tool: str) -> None:
        for key in TEXT_KEYWORDS:
            old, new = before.get(key), after.get(key)
            if old == new:
                continue
            self.add(
                "informational",
                "description.changed",
                tool,
                _join(path, key),
                f"{key} {_delta_word(old, new)}",
                old,
                new,
            )

    def _examples(self, before: dict, after: dict, path: str, tool: str) -> None:
        for key in EXAMPLE_KEYWORDS:
            old, new = before.get(key), after.get(key)
            if _same(old, new):
                continue
            self.add(
                "informational",
                "examples.changed",
                tool,
                _join(path, key),
                f"{key} {_delta_word(old, new)}",
                old,
                new,
            )

    def _type(
        self, before: dict, after: dict, path: str, tool: str, in_output: bool
    ) -> None:
        old, new = before.get("type"), after.get("type")
        if _same(old, new):
            return
        type_path = _join(path, "type")
        old_types, new_types = _type_set(old), _type_set(new)
        if old_types is None and new_types is None:
            self.add("risky", "schema.changed", tool, type_path, "type changed", old, new)
            return
        if old_types is None:
            self.add(
                "risky",
                "constraint.tightened",
                tool,
                type_path,
                "type constraint added",
                old,
                new,
            )
            return
        if new_types is None:
            self.add(
                "risky",
                "constraint.loosened",
                tool,
                type_path,
                "type constraint removed",
                old,
                new,
            )
            return
        if old_types == new_types:
            return
        dropped = sorted(
            name
            for name in old_types - new_types
            if not _still_accepted(name, new_types)
        )
        if dropped:
            rule = "output.type.incompatible" if in_output else "type.incompatible"
            self.add(
                "breaking",
                rule,
                tool,
                type_path,
                f"type no longer accepts {', '.join(dropped)}",
                old,
                new,
            )
            return
        self.add("risky", "type.widened", tool, type_path, "type widened", old, new)

    def _allowed(self, before: dict, after: dict, path: str, tool: str) -> None:
        old, old_kind = _allowed_values(before)
        new, new_kind = _allowed_values(after)
        if old is None and new is None:
            return
        value_path = _join(path, new_kind or old_kind or "enum")
        if old is None:
            self.add(
                "breaking",
                "enum.narrowed",
                tool,
                value_path,
                f"{new_kind} added, restricting the allowed values",
                None,
                new,
            )
            return
        if new is None:
            self.add(
                "risky",
                "enum.widened",
                tool,
                value_path,
                f"{old_kind} removed, no longer restricting the allowed values",
                old,
                None,
            )
            return
        old_set, new_set = _value_set(old), _value_set(new)
        if old_set == new_set:
            if old_kind == new_kind and old != new:
                self.add(
                    "informational",
                    "enum.reordered",
                    tool,
                    value_path,
                    "enum reordered, the allowed values are unchanged",
                    old,
                    new,
                )
            return
        if old_set - new_set:
            if old_kind == "const" and new_kind == "const":
                self.add(
                    "breaking",
                    "const.changed",
                    tool,
                    value_path,
                    "const value changed",
                    old[0],
                    new[0],
                )
            else:
                self.add(
                    "breaking",
                    "enum.narrowed",
                    tool,
                    value_path,
                    "allowed values were removed",
                    old,
                    new,
                )
            return
        self.add(
            "risky",
            "enum.widened",
            tool,
            value_path,
            "allowed values were added",
            old,
            new,
        )

    def _constraints(self, before: dict, after: dict, path: str, tool: str) -> None:
        for key in MIN_CONSTRAINTS + MAX_CONSTRAINTS:
            old, new = before.get(key), after.get(key)
            if _same(old, new):
                continue
            key_path = _join(path, key)
            if old is None:
                self.add(
                    "risky",
                    "constraint.tightened",
                    tool,
                    key_path,
                    f"{key} added",
                    old,
                    new,
                )
                continue
            if new is None:
                self.add(
                    "risky",
                    "constraint.loosened",
                    tool,
                    key_path,
                    f"{key} removed",
                    old,
                    new,
                )
                continue
            if not (_is_number(old) and _is_number(new)):
                self.add(
                    "risky", "schema.changed", tool, key_path, f"{key} changed", old, new
                )
                continue
            stricter = new > old if key in MIN_CONSTRAINTS else new < old
            rule = "constraint.tightened" if stricter else "constraint.loosened"
            word = "tightened" if stricter else "loosened"
            self.add(
                "risky", rule, tool, key_path, f"{key} {word} from {old} to {new}", old, new
            )

    def _keyword(
        self, before: dict, after: dict, path: str, tool: str, key: str, rule: str
    ) -> None:
        old, new = before.get(key), after.get(key)
        if _same(old, new):
            return
        self.add(
            "risky",
            rule,
            tool,
            _join(path, key),
            f"{key} {_delta_word(old, new)}",
            old,
            new,
        )

    def _additional_properties(
        self, before: dict, after: dict, path: str, tool: str, in_output: bool
    ) -> None:
        old, new = before.get("additionalProperties"), after.get("additionalProperties")
        key_path = _join(path, "additionalProperties")
        if isinstance(old, dict) and isinstance(new, dict):
            self.walk(old, new, key_path, tool, in_output)
            return
        if _same(old, new):
            return
        self.add(
            "risky",
            "additionalProperties.changed",
            tool,
            key_path,
            f"additionalProperties {_delta_word(old, new)}",
            old,
            new,
        )

    def _object(
        self, before: dict, after: dict, path: str, tool: str, in_output: bool
    ) -> None:
        old_props = before.get("properties")
        new_props = after.get("properties")
        old_props = old_props if isinstance(old_props, dict) else {}
        new_props = new_props if isinstance(new_props, dict) else {}
        old_required = self._required(before)
        new_required = self._required(after)

        for key in sorted(set(old_props) | set(new_props)):
            key_path = _join(_join(path, "properties"), key)
            if key in old_props and key in new_props:
                self._subschema(
                    old_props[key],
                    new_props[key],
                    key_path,
                    tool,
                    in_output,
                    f"property {key!r} changed",
                )
            elif key in old_props:
                rule = "output.field.removed" if in_output else "arg.removed"
                noun = "output field" if in_output else "property"
                self.add(
                    "breaking",
                    rule,
                    tool,
                    key_path,
                    f"{noun} {key!r} was removed",
                    old_props[key],
                    None,
                )
            elif in_output:
                self.add(
                    "informational",
                    "output.field.optional.added",
                    tool,
                    key_path,
                    f"output field {key!r} was added",
                    None,
                    new_props[key],
                )
            elif key in new_required:
                self.add(
                    "breaking",
                    "arg.required.added",
                    tool,
                    key_path,
                    f"required property {key!r} was added",
                    None,
                    new_props[key],
                )
            else:
                self.add(
                    "informational",
                    "arg.optional.added",
                    tool,
                    key_path,
                    f"optional property {key!r} was added",
                    None,
                    new_props[key],
                )

        # Required deltas for properties that exist on both sides. Required
        # changes that come with the property itself are already reported
        # above, so they are not counted twice.
        shared = set(old_props) & set(new_props)
        if not old_props and not new_props:
            shared = old_required | new_required
        for key in sorted((new_required - old_required) & shared):
            key_path = _join(_join(path, "required"), key)
            if in_output:
                self.add(
                    "risky",
                    "schema.changed",
                    tool,
                    key_path,
                    f"output field {key!r} became required",
                    False,
                    True,
                )
            else:
                self.add(
                    "breaking",
                    "arg.required.added",
                    tool,
                    key_path,
                    f"property {key!r} became required",
                    False,
                    True,
                )
        for key in sorted((old_required - new_required) & shared):
            key_path = _join(_join(path, "required"), key)
            if in_output:
                self.add(
                    "risky",
                    "schema.changed",
                    tool,
                    key_path,
                    f"output field {key!r} is no longer required",
                    True,
                    False,
                )
            else:
                self.add(
                    "risky",
                    "arg.required.removed",
                    tool,
                    key_path,
                    f"property {key!r} is no longer required",
                    True,
                    False,
                )

    @staticmethod
    def _required(node: dict[str, Any]) -> set[str]:
        required = node.get("required")
        if not isinstance(required, list):
            return set()
        return {item for item in required if isinstance(item, str)}

    def _arrays(
        self, before: dict, after: dict, path: str, tool: str, in_output: bool
    ) -> None:
        old, new = before.get("items"), after.get("items")
        items_path = _join(path, "items")
        if isinstance(old, list) and isinstance(new, list):
            self._schema_list(old, new, items_path, tool, in_output)
        else:
            self._subschema(old, new, items_path, tool, in_output, "items changed")

        old, new = before.get("prefixItems"), after.get("prefixItems")
        prefix_path = _join(path, "prefixItems")
        if isinstance(old, list) and isinstance(new, list):
            self._schema_list(old, new, prefix_path, tool, in_output)
        elif not _same(old, new):
            self.add(
                "risky",
                "schema.changed",
                tool,
                prefix_path,
                f"prefixItems {_delta_word(old, new)}",
                old,
                new,
            )

    def _schema_list(
        self,
        before: list[Any],
        after: list[Any],
        path: str,
        tool: str,
        in_output: bool,
    ) -> None:
        if len(before) != len(after):
            self.add(
                "risky",
                "schema.changed",
                tool,
                path,
                f"positional schema count changed from {len(before)} to {len(after)}",
                before,
                after,
            )
            return
        for index, (old, new) in enumerate(zip(before, after)):
            self._subschema(
                old, new, f"{path}[{index}]", tool, in_output, "positional schema changed"
            )

    def _unions(
        self, before: dict, after: dict, path: str, tool: str, in_output: bool
    ) -> None:
        for key in UNION_KEYWORDS:
            old, new = before.get(key), after.get(key)
            if _same(old, new):
                continue
            key_path = _join(path, key)
            if not isinstance(old, list) or not isinstance(new, list):
                self.add(
                    "risky",
                    "union.changed",
                    tool,
                    key_path,
                    f"{key} {_delta_word(old, new)}",
                    old,
                    new,
                )
                continue
            if _value_set(old) == _value_set(new):
                # Same branches in a different order. Order is not semantic.
                continue
            if len(old) != len(new):
                self.add(
                    "risky",
                    "union.changed",
                    tool,
                    key_path,
                    f"{key} membership changed from {len(old)} to {len(new)} branches",
                    old,
                    new,
                )
                continue
            self._schema_list(old, new, key_path, tool, in_output)

    def _rest(self, before: dict, after: dict, path: str, tool: str) -> None:
        for key in sorted(set(before) | set(after)):
            if key in HANDLED_KEYWORDS:
                continue
            old, new = before.get(key), after.get(key)
            if _same(old, new):
                continue
            self.add(
                "risky",
                "schema.changed",
                tool,
                _join(path, key),
                f"{key} {_delta_word(old, new)}",
                old,
                new,
            )


def classify(baseline: list[Tool], candidate: list[Tool]) -> list[Change]:
    """Classify every difference between two tool rosters.

    Both sides are canonicalized first, so key order and description
    whitespace never register as changes. The result is sorted by severity,
    then tool name, then rule id, then path.
    """
    classifier = _Classifier()
    classifier.compare_rosters(
        canonicalize_tools(baseline), canonicalize_tools(candidate)
    )
    return sorted(classifier.changes, key=lambda change: change.sort_key)


def count_by_severity(changes: list[Change]) -> dict[str, int]:
    """Return counts keyed ``breaking``, ``risky``, ``informational``."""
    counts = {name: 0 for name in reversed(SEVERITY_ORDER)}
    for change in changes:
        counts[change.severity] += 1
    return counts


def reaches(changes: list[Change], severity: str) -> bool:
    """True when any change is at least as severe as ``severity``."""
    if severity not in SEVERITY_RANK:
        return False
    threshold = SEVERITY_RANK[severity]
    return any(SEVERITY_RANK[change.severity] >= threshold for change in changes)
