"""Tests for the authentication flow: register → login → access protected route."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from main import app
from database import get_db
from models.base import Base
import os


# --- Test database: in-memory SQLite, fresh per test session ---
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/campusreserve_test",
)
engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def client():
    """A test client backed by a fresh in-memory database."""

    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        conn.commit()
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)

    # teardown: drop everything and clear the override
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


def test_register_creates_user(client):
    resp = client.post("/auth/register", json={
        "email": "leader@stonybrook.edu",
        "name": "Test Leader",
        "password": "secret123",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "leader@stonybrook.edu"
    assert body["role"] == "club_leader"
    assert "password" not in body          # never leak the password
    assert "password_hash" not in body     # never leak the hash


def test_register_duplicate_email_rejected(client):
    payload = {"email": "dup@stonybrook.edu", "name": "A", "password": "pw12345"}
    client.post("/auth/register", json=payload)
    resp = client.post("/auth/register", json=payload)
    assert resp.status_code == 409


def test_login_and_access_protected_route(client):
    client.post("/auth/register", json={
        "email": "me@stonybrook.edu",
        "name": "Me",
        "password": "secret123",
    })

    # log in — OAuth2 form uses "username" for the email field
    login = client.post("/auth/token", data={
        "username": "me@stonybrook.edu",
        "password": "secret123",
    })
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert token

    # use the token on a protected endpoint
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "me@stonybrook.edu"


def test_login_wrong_password_rejected(client):
    client.post("/auth/register", json={
        "email": "x@stonybrook.edu",
        "name": "X",
        "password": "correct-pw",
    })
    resp = client.post("/auth/token", data={
        "username": "x@stonybrook.edu",
        "password": "wrong-pw",
    })
    assert resp.status_code == 401


def test_protected_route_without_token_rejected(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401
