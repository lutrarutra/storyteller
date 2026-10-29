"""Packs table columns into one compact binary blob that the report's runtime decodes.

Layout: the columns' little-endian buffers back to back (each aligned to 4 bytes),
gzip-compressed. The column descriptions (name, kind, offset, length, categories)
travel in the explorer's JSON spec. Kinds:

- ``f32``: numbers as float32 (NaN for missing values);
- ``u8``: booleans as 0/1;
- ``cat``: categories as uint16 codes (65535 = missing) plus the category labels.
"""

from __future__ import annotations

import gzip
from typing import Any

MISSING_CODE = 65535


def column_values(data: Any, name: str) -> Any:
    """One column of a DataFrame, mapping of columns or AnnData-like `.obs`, as a numpy array."""
    import numpy as np

    if hasattr(data, "to_pandas") and not hasattr(data, "iloc"):
        data = data.to_pandas()
    try:
        column = data[name]
    except (KeyError, IndexError, TypeError):
        raise KeyError(f"no column {name!r} in the data") from None
    if hasattr(column, "cat"):  # pandas categorical: keep its category order
        return column
    return column.to_numpy() if hasattr(column, "to_numpy") else np.asarray(column)


def pack(data: Any, names: list[str]) -> tuple[bytes, list[dict[str, Any]], int]:
    """(gzip bytes, column descriptions, row count) for the named columns."""
    import numpy as np

    body = bytearray()
    columns = []
    n_rows = None
    for name in names:
        values = column_values(data, name)
        if hasattr(values, "cat"):
            categories = [str(c) for c in values.cat.categories]
            codes = values.cat.codes.to_numpy().astype(np.int64)
            kind = "cat"
        else:
            array = np.asarray(values)
            if array.dtype.kind == "b":
                kind, encoded = "u8", array.astype("<u1")
            elif array.dtype.kind in "iuf":
                kind, encoded = "f32", array.astype("<f4")
            else:
                kind = "cat"
                labels = np.array(["" if v is None else str(v) for v in array.tolist()], dtype=object)
                missing = np.array([v is None or v != v for v in array.tolist()], dtype=bool)
                categories = sorted(set(labels[~missing].tolist()))
                lookup = {c: i for i, c in enumerate(categories)}
                codes = np.array([-1 if m else lookup[v] for v, m in zip(labels, missing, strict=True)], dtype=np.int64)
        if kind == "cat":
            if len(categories) >= MISSING_CODE:
                raise ValueError(f"column {name!r} has too many categories ({len(categories)}) to filter by")
            encoded = np.where(codes < 0, MISSING_CODE, codes).astype("<u2")
        if n_rows is None:
            n_rows = len(encoded)
        elif len(encoded) != n_rows:
            raise ValueError(f"column {name!r} has {len(encoded)} rows, expected {n_rows}")
        while len(body) % 4:
            body.append(0)
        description = {"name": name, "kind": kind, "offset": len(body), "length": len(encoded)}
        if kind == "cat":
            description["categories"] = categories
        columns.append(description)
        body.extend(encoded.tobytes())
    return gzip.compress(bytes(body), compresslevel=9, mtime=0), columns, n_rows or 0


def unpack(blob: bytes, columns: list[dict[str, Any]]) -> dict[str, Any]:
    """The inverse of `pack` (used by tests and by the static snapshot)."""
    import numpy as np

    body = gzip.decompress(blob)
    out = {}
    for column in columns:
        dtype = {"f32": "<f4", "u8": "<u1", "cat": "<u2"}[column["kind"]]
        values = np.frombuffer(body, dtype=dtype, count=column["length"], offset=column["offset"])
        if column["kind"] == "u8":
            values = values.astype(bool)
        elif column["kind"] == "cat":
            labels = np.array([*column["categories"], None], dtype=object)
            values = labels[np.where(values == MISSING_CODE, len(column["categories"]), values)]
        out[column["name"]] = values
    return out
