"""
Shared CSV-download helper for the list grids.

The grids on the Users, Groups, Metadata and Archiver pages all use AG Grid's
infinite row model, so `gridApi.exportDataAsCsv()` only ever serializes the
blocks AG Grid happens to have cached — a few hundred rows out of a cluster
that may hold tens of thousands. An admin exporting "the users" and silently
getting the first two screens of them is worse than no export at all.

So exports are served from the backend instead: page through the same service
function the grid calls, and stream the rows out as they are read rather than
buffering the whole file. `EXPORT_PAGE_SIZE` matches the `le=1000` cap the list
endpoints already enforce.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timezone
from typing import Any

from fastapi.responses import StreamingResponse

EXPORT_PAGE_SIZE = 1000


def paged_rows(
    fetch: Callable[[int, int], tuple[list[dict], int]],
    *,
    page_size: int | None = None,
) -> Iterator[dict]:
    """
    Yield every row of a `(items, total)` paginated service function.

    `fetch(record_offset, page_size)` is the service call. Termination is on a
    short page, not on `total`: a sync completing mid-export would otherwise
    move `total` and either truncate the file or spin forever.

    `page_size` defaults to the module-level `EXPORT_PAGE_SIZE` and is read at
    call time, not bound at import, so a test can shrink it to force the
    multi-page path.
    """
    page_size = page_size or EXPORT_PAGE_SIZE
    offset = 0
    while True:
        items, _total = fetch(offset, page_size)
        if not items:
            return
        yield from items
        if len(items) < page_size:
            return
        offset += page_size


def _cell(value: Any) -> str:
    """Render one value the way the grid does — lists joined, None blank."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def csv_response(
    *,
    filename_stem: str,
    columns: Sequence[tuple[str, str]],
    rows: Iterator[dict],
) -> StreamingResponse:
    """
    Stream `rows` as a CSV download.

    `columns` is an ordered list of `(dict key, header label)` pairs — the
    header labels match the grid's column headers so a file opened in Excel
    reads the same as the screen it came from.
    """

    def generate() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")

        def flush() -> str:
            out = buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
            return out

        writer.writerow([label for _key, label in columns])
        yield flush()
        for row in rows:
            writer.writerow([_cell(row.get(key)) for key, _label in columns])
            yield flush()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{filename_stem}-{stamp}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
