"""Dev-only runner for the rooms router, with no login and no database.
Not for production. For the full app, use `uvicorn main:app --reload`.

Run from the server/ folder:
    uvicorn dev_rooms:app --reload
Then open http://127.0.0.1:8000/docs (shows 25Live data only).

On startup it begins caching every room's bookings in the background; watch
the terminal for "25Live refresh: N/N rooms". Availability searches are fast
once that finishes.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from authentication import get_current_user
from database import get_db
from routers import rooms
from services import live25

logging.basicConfig(level=logging.INFO)
# Don't log every 25Live request; the refresh prints a summary line instead.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(live25.run_refresh_loop())
    yield
    task.cancel()


app = FastAPI(title="CampusReserve rooms (dev, 25Live only)", lifespan=lifespan)
app.include_router(rooms.router)

app.dependency_overrides[get_current_user] = lambda: None
app.dependency_overrides[get_db] = lambda: None
rooms._room_list_data = lambda db: ({}, {}, {})
rooms._schedule_data = lambda db, room_id, start, end: (None, [], [], [])
rooms._availability_db_data = lambda db, start, end: (set(), {})
