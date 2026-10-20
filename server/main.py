import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from database import get_db
from routers import admin, auth, me, notifications, organizations, requests, rooms, waitlist
from services import live25
from services.request_flow import run_waitlist_loop

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log every 25Live request


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Background jobs: keep the 25Live cache fresh, and move waitlisted
    # requests up when their slot opens.
    tasks = [
        asyncio.create_task(live25.run_refresh_loop()),
        asyncio.create_task(run_waitlist_loop(get_db)),
    ]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="CampusReserve", lifespan=lifespan)

# ADDED: without this, the browser blocks any request the React frontend
# (Vite, localhost:5173) tries to make to this API (localhost:8000) -- a
# browser security rule, not a bug in either side. curl/the /docs page
# still work fine without it, since those aren't cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(rooms.router)
app.include_router(requests.router)
app.include_router(waitlist.router)
app.include_router(organizations.router)
app.include_router(notifications.router)
app.include_router(me.router)
app.include_router(admin.router)
