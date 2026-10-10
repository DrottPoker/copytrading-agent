from typing import Any

import pytest

from app.db.session import affected_row_count


class FakeResult:
    def __init__(self, rowcount: Any) -> None:
        self.rowcount = rowcount


@pytest.mark.parametrize(
    ("rowcount", "expected"),
    [
        (3, 3),
        (0, 0),
        (None, 0),
        # DBAPI drivers report -1 when the affected row count is unknown.
        (-1, 0),
    ],
)
def test_affected_row_count_never_reports_negative_rows(rowcount: Any, expected: int) -> None:
    assert affected_row_count(FakeResult(rowcount)) == expected
