from __future__ import annotations

import csv

from ytdt.tabular import write_key_values, write_table


def test_write_table_csv_with_position(tmp_path):
    rows = [{"a": 1, "b": "x,y"}, {"a": 2, "b": "plain"}]
    path = write_table(rows, tmp_path / "out.csv")
    with path.open(newline="") as fh:
        parsed = list(csv.reader(fh))
    assert parsed[0] == ["position", "a", "b"]
    assert parsed[1] == ["1", "1", "x,y"]  # csv module quotes embedded commas
    assert parsed[2] == ["2", "2", "plain"]


def test_write_table_accepts_to_row_objects(tmp_path):
    class Row:
        def to_row(self):
            return {"k": "v"}

    path = write_table([Row()], tmp_path / "out.csv", position=False)
    assert path.read_text().splitlines() == ["k", "v"]


def test_write_key_values(tmp_path):
    path = write_key_values({"title": "T", "views": 5}, tmp_path / "info.csv")
    assert path.read_text().splitlines() == ["title,T", "views,5"]
