"""Pagination for list endpoints: ?limit=N&offset=M.

Both are optional. Without them an endpoint returns what it always did, so
existing callers keep working. Every paginated response also carries the total
number of matching items in an X-Total-Count header, for "Page 2 of 9":

    GET /requests/mine?limit=10&offset=10      -> items 11-20
    X-Total-Count: 87

In an endpoint:

    def list_things(response: Response, page: Page = Depends(paged()), db=...):
        q = db.query(Thing).order_by(Thing.created_at.desc(), Thing.id)
        return [out(t) for t in page.query(q, response).all()]       # SQL query
        # or, for a list already built in Python:
        return [out(t) for t in page.items(things, response)]

Order by something unique (add the id as a tie-breaker), or rows with equal
sort keys can swap between pages.

The browser can only read X-Total-Count if CORS exposes it; main.py needs
expose_headers=["X-Total-Count"] on the CORSMiddleware.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

from fastapi import Query, Response

TOTAL_HEADER = "X-Total-Count"
MAX_LIMIT = 500

T = TypeVar("T")


@dataclass(frozen=True)
class Page:
    limit: int | None     # None = no limit
    offset: int = 0

    def _set_total(self, response: Response | None, total: int) -> None:
        if response is not None:
            response.headers[TOTAL_HEADER] = str(total)

    def query(self, q, response: Response | None = None):
        """Count the matching rows, then return the query for just this page."""
        self._set_total(response, q.order_by(None).count())
        if self.offset:
            q = q.offset(self.offset)
        if self.limit is not None:
            q = q.limit(self.limit)
        return q

    def items(self, items: Sequence[T], response: Response | None = None) -> list[T]:
        """The same, for a list that's already in memory."""
        self._set_total(response, len(items))
        end = None if self.limit is None else self.offset + self.limit
        return list(items[self.offset:end])


def paged(default_limit: int | None = None, max_limit: int = MAX_LIMIT):
    """A FastAPI dependency that reads ?limit= and ?offset= into a Page.

    default_limit: how many to return when the caller doesn't say (None = all).
    max_limit: the largest limit a caller may ask for (larger values get a 422).
    """
    def dependency(
        limit: int | None = Query(default_limit, ge=1, le=max_limit,
                                  description="How many to return (leave out for "
                                  + ("all" if default_limit is None else str(default_limit)) + ")"),
        offset: int = Query(0, ge=0, description="How many to skip first"),
    ) -> Page:
        return Page(limit=limit, offset=offset)
    return dependency
