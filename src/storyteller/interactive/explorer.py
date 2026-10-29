"""Linked controls and views over one embedded dataset.

Controls decide which rows count:

- `Select` and `Toggle` choose a subset (e.g. one sample); rows outside it leave every view.
- `Threshold` sets pass criteria (e.g. n_genes ≥ 200). In "highlight" mode (the default,
  made for QC) views show all rows of the subset coloured pass/fail; in "filter" mode
  rows that fail leave the views.

Views draw the result and update when a control moves. The same rules are applied
here in Python for the static snapshot and the initial numbers, and in runtime.js
in the browser; tests check that both agree.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# -- controls --------------------------------------------------------------------


@dataclass(frozen=True)
class Threshold:
    """Rows pass when `min <= value <= max` (either bound may be left open)."""

    column: str
    min: float | None = None
    max: float | None = None
    label: str | None = None
    step: float | None = None

    def __post_init__(self) -> None:
        if self.min is None and self.max is None:
            raise ValueError(f"Threshold({self.column!r}) needs min=, max= or both")

    kind = "threshold"


@dataclass(frozen=True)
class Select:
    """Restricts the views to rows whose `column` is one of `value` (None: all values)."""

    column: str
    value: str | list[str] | None = None
    label: str | None = None
    multiple: bool = False

    kind = "select"


@dataclass(frozen=True)
class Toggle:
    """When on, restricts the views to rows where the boolean `column` is true."""

    column: str
    value: bool = False
    label: str | None = None

    kind = "toggle"


Control = Threshold | Select | Toggle

# -- views -------------------------------------------------------------------------


@dataclass(frozen=True)
class Histogram:
    column: str
    bins: int = 60
    log_x: bool = False
    log_y: bool = False
    title: str | None = None

    kind = "histogram"


@dataclass(frozen=True)
class Scatter:
    x: str
    y: str
    color: str | None = "pass"  # "pass" (pass/fail), a column name, or None
    log_x: bool = False
    log_y: bool = False
    max_points: int = 20_000
    title: str | None = None

    kind = "scatter"


@dataclass(frozen=True)
class Bar:
    """Rows per category of `column`, split into passing and failing."""

    column: str
    title: str | None = None
    horizontal: bool = False

    kind = "bar"


@dataclass(frozen=True)
class Box:
    """Distribution of `column` per category of `by`."""

    column: str
    by: str | None = None
    log_y: bool = False
    title: str | None = None

    kind = "box"


@dataclass(frozen=True)
class Table:
    """The first `n` rows that are shown (subset, and passing in filter mode)."""

    columns: list[str] = field(default_factory=list)
    n: int = 20
    title: str | None = None

    kind = "table"


@dataclass(frozen=True)
class Stat:
    """A sentence with live numbers. Fields: n, n_pass, n_fail, pct_pass, pct_fail, with Python
    format specs, e.g. "{n_pass:,} of {n:,} cells pass ({pct_pass:.1%})"."""

    template: str

    kind = "stat"


View = Histogram | Scatter | Bar | Box | Table | Stat

STAT_FIELDS = ("n", "n_pass", "n_fail", "pct_pass", "pct_fail")
# The format specs runtime.js understands: fill/align and sign are not supported.
_SPEC = re.compile(r"\{(\w+)(?::(,)?(?:\.(\d+))?([fd%])?)?\}")


def spec_of(item: Control | View) -> dict[str, Any]:
    return {"kind": item.kind, **{k: v for k, v in asdict(item).items() if v is not None}}


def columns_used(controls: list[Control], views: list[View]) -> list[str]:
    names: list[str] = []
    for item in [*controls, *views]:
        if isinstance(item, Scatter):
            candidates = [item.x, item.y, item.color if item.color not in (None, "pass") else None]
        elif isinstance(item, Box):
            candidates = [item.column, item.by]
        elif isinstance(item, Table):
            candidates = list(item.columns)
        elif isinstance(item, Stat):
            candidates = []
        else:
            candidates = [item.column]
        names += [c for c in candidates if c and c not in names]
    return names


def check_template(template: str) -> None:
    for match in re.finditer(r"\{[^}]*\}", template):
        parsed = _SPEC.fullmatch(match.group(0))
        if not parsed or parsed.group(1) not in STAT_FIELDS:
            raise ValueError(
                f"Stat template field {match.group(0)!r}: use {{name}} or {{name:,}}, {{name:.1f}}, {{name:.1%}} "
                f"with name one of {', '.join(STAT_FIELDS)}"
            )


def format_template(template: str, values: dict[str, float]) -> str:
    return _SPEC.sub(lambda m: _format(values[m.group(1)], m.group(2), m.group(3), m.group(4)), template)


def _format(value: float, comma: str | None, digits: str | None, kind: str | None) -> str:
    if kind == "%":
        return f"{value * 100:{',' if comma else ''}.{digits or 0}f}%"
    if kind == "d" or (kind is None and digits is None and float(value).is_integer()):
        return f"{round(value):{',' if comma else ''}d}"
    return f"{value:{',' if comma else ''}.{digits if digits is not None else 2}f}"


# -- evaluation --------------------------------------------------------------------


def masks(columns: dict[str, Any], controls: list[Control], settings: dict[str, Any] | None = None):
    """(in_subset, passes) boolean arrays for the given control settings (default: their initial values)."""
    import numpy as np

    n = len(next(iter(columns.values()))) if columns else 0
    subset = np.ones(n, dtype=bool)
    passes = np.ones(n, dtype=bool)
    settings = settings or {}
    for control in controls:
        values = columns[control.column]
        setting = settings.get(control.column, {})
        if isinstance(control, Threshold):
            numbers = np.asarray(values, dtype=float)
            low = setting.get("min", control.min)
            high = setting.get("max", control.max)
            ok = np.isfinite(numbers)
            if low is not None:
                ok &= numbers >= low
            if high is not None:
                ok &= numbers <= high
            passes &= ok
        elif isinstance(control, Select):
            chosen = setting.get("value", control.value)
            if chosen not in (None, [], ""):
                chosen = [chosen] if isinstance(chosen, str) else list(chosen)
                subset &= np.isin(np.asarray(values, dtype=object), chosen)
        elif isinstance(control, Toggle) and setting.get("value", control.value):
            subset &= np.asarray(values, dtype=bool)
    return subset, passes


def stats(subset: Any, passes: Any) -> dict[str, float]:
    n = int(subset.sum())
    n_pass = int((subset & passes).sum())
    return {
        "n": n,
        "n_pass": n_pass,
        "n_fail": n - n_pass,
        "pct_pass": n_pass / n if n else 0.0,
        "pct_fail": (n - n_pass) / n if n else 0.0,
    }


def settings_text(controls: list[Control]) -> str:
    """The initial settings in words, e.g. "n_genes ≥ 200 · pct_mt ≤ 15 · sample: all"."""
    parts = []
    for control in controls:
        name = control.label or control.column
        if isinstance(control, Threshold):
            if control.min is not None and control.max is not None:
                parts.append(f"{control.min:g} ≤ {name} ≤ {control.max:g}")
            elif control.min is not None:
                parts.append(f"{name} ≥ {control.min:g}")
            else:
                parts.append(f"{name} ≤ {control.max:g}")
        elif isinstance(control, Select):
            value = control.value
            shown = "all" if value in (None, [], "") else value if isinstance(value, str) else ", ".join(value)
            parts.append(f"{name}: {shown}")
        elif isinstance(control, Toggle):
            parts.append(f"{name}: {'on' if control.value else 'off'}")
    return " · ".join(parts)


Mode = Literal["highlight", "filter"]
