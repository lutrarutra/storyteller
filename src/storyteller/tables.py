"""Tables from DataFrames, dicts of columns or lists of rows, rendered once into escaped HTML."""

from __future__ import annotations

import math
import numbers
from collections.abc import Callable, Mapping, Sequence
from html import escape
from typing import Any

Formatter = str | Callable[[Any], str]


def _columns_and_rows(data: Any, index: bool | None) -> tuple[list[str], list[list[Any]], list[str] | None]:
    """Column names, row values and (optionally) row labels for any supported table input."""
    if hasattr(data, "to_pandas") and not hasattr(data, "iloc"):  # polars, pyarrow
        data = data.to_pandas()
    if hasattr(data, "iloc") and hasattr(data, "columns"):  # pandas DataFrame
        columns = [str(c) for c in data.columns]
        rows = data.to_numpy(dtype=object).tolist()
        show_index = index if index is not None else not _is_default_index(data.index)
        labels = [str(v) for v in data.index] if show_index else None
        return columns, rows, labels
    if hasattr(data, "iloc") and hasattr(data, "name"):  # pandas Series
        return _columns_and_rows(data.to_frame(), index if index is not None else True)
    if isinstance(data, Mapping):
        columns = [str(c) for c in data]
        values = [list(v) if not isinstance(v, (str, bytes)) else [v] for v in data.values()]
        length = max((len(v) for v in values), default=0)
        rows = [[v[i] if i < len(v) else None for v in values] for i in range(length)]
        return columns, rows, None
    if isinstance(data, Sequence) and data and isinstance(data[0], Mapping):
        columns = list(dict.fromkeys(str(k) for row in data for k in row))
        return columns, [[row.get(c) for c in columns] for row in data], None
    if isinstance(data, Sequence):
        rows = [list(row) for row in data]
        width = max((len(r) for r in rows), default=0)
        return [str(i + 1) for i in range(width)], rows, None
    raise TypeError(f"cannot show a {type(data).__name__} as a table")


def _is_default_index(index: Any) -> bool:
    try:
        import pandas as pd

        return isinstance(index, pd.RangeIndex) and index.start == 0 and index.step == 1 and index.name is None
    except ImportError:
        return True


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)  # NaN, NaT, pd.NA (raises)
    except (TypeError, ValueError):
        return False


def format_value(value: Any, formatter: Formatter | None = None) -> str:
    if _is_missing(value):
        return ""
    if formatter is not None:
        return formatter(value) if callable(formatter) else formatter.format(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, numbers.Integral):
        return f"{int(value):,}"
    if isinstance(value, numbers.Real):
        number = float(value)
        if number.is_integer() and abs(number) < 1e15:
            return f"{int(number):,}"
        magnitude = abs(number)
        if magnitude >= 1e6 or magnitude < 1e-3:
            return f"{number:.3g}"
        if magnitude >= 100:
            return f"{number:,.1f}"
        return f"{number:.3g}" if magnitude < 1 else f"{number:.2f}"
    return str(value)


def _flag_cells(flags: Any, n_rows: int, columns: list[str]) -> Callable[[int, int], bool]:
    """flags: a boolean per row, a frame/dict of booleans per cell, or a function(row, column) → bool."""
    if flags is None:
        return lambda r, c: False
    if callable(flags):
        return lambda r, c: bool(flags(r, columns[c]))
    if hasattr(flags, "columns") or isinstance(flags, Mapping):
        cell_columns, cell_rows, _ = _columns_and_rows(flags, index=False)
        lookup = {name: i for i, name in enumerate(cell_columns)}
        return lambda r, c: columns[c] in lookup and r < len(cell_rows) and bool(cell_rows[r][lookup[columns[c]]])
    per_row = [bool(v) for v in (flags.tolist() if hasattr(flags, "tolist") else list(flags))]
    if len(per_row) != n_rows:
        raise ValueError(f"flags has {len(per_row)} values for {n_rows} rows")
    return lambda r, c: per_row[r]


def render(
    data: Any,
    *,
    format: Mapping[str, Formatter] | None = None,
    flags: Any = None,
    max_rows: int = 50,
    index: bool | None = None,
    sortable: bool = True,
) -> tuple[str, dict[str, int]]:
    """The table's HTML and its shape ({"rows": total, "shown": shown, "columns": n})."""
    columns, rows, labels = _columns_and_rows(data, index)
    formats = dict(format or {})
    is_flagged = _flag_cells(flags, len(rows), columns)
    numeric = [
        all(isinstance(row[i], numbers.Number) or _is_missing(row[i]) for row in rows)
        and any(isinstance(row[i], numbers.Number) and not _is_missing(row[i]) for row in rows)
        for i in range(len(columns))
    ]
    shown = rows if len(rows) <= max_rows else rows[:max_rows]
    parts = [f'<table class="cf-table"{" data-cf-sortable" if sortable else ""}><thead><tr>']
    if labels is not None:
        parts.append('<th scope="col" class="cf-index"></th>')
    for name, is_numeric in zip(columns, numeric, strict=True):
        parts.append(f'<th scope="col"{" class=cf-num" if is_numeric else ""}>{escape(name)}</th>')
    parts.append("</tr></thead><tbody>")
    for r, row in enumerate(shown):
        parts.append("<tr>")
        if labels is not None:
            parts.append(f'<th scope="row" class="cf-index">{escape(labels[r])}</th>')
        for c, value in enumerate(row):
            classes = []
            if numeric[c]:
                classes.append("cf-num")
            if is_flagged(r, c):
                classes.append("cf-flag")
            attrs = f' class="{" ".join(classes)}"' if classes else ""
            sort_key = ""
            if numeric[c] and not _is_missing(value) and math.isfinite(float(value)):
                sort_key = f' data-v="{float(value)!r}"'
            parts.append(f"<td{attrs}{sort_key}>{escape(format_value(value, formats.get(columns[c])))}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts), {"rows": len(rows), "shown": len(shown), "columns": len(columns)}
