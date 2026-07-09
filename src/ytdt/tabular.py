"""CSV export helpers."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Mapping


def write_table(
    rows: Iterable[Mapping[str, Any] | Any],
    path: str | Path,
    *,
    position: bool = True,
) -> Path:
    """Write dict rows (or objects with ``to_row()``) to a CSV file.

    ``position`` prepends a 1-based rank column, mirroring the original
    YTDT output where row order encodes search ranking.
    """
    path = Path(path)
    materialized = [row.to_row() if hasattr(row, "to_row") else dict(row) for row in rows]
    fieldnames: list[str] = []
    for row in materialized:
        for name in row:
            if name not in fieldnames:
                fieldnames.append(name)
    if position:
        fieldnames.insert(0, "position")
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for index, row in enumerate(materialized, start=1):
            if position:
                row = {"position": index, **row}
            writer.writerow(row)
    return path


def write_key_values(mapping: Mapping[str, Any], path: str | Path) -> Path:
    """Write a two-column key/value CSV file."""
    path = Path(path)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        for key, value in mapping.items():
            writer.writerow([key, value])
    return path
