"""Shared fixtures for family-tasks backend tests."""
import os
import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

# Load frontend .env for EXPO_PUBLIC_BACKEND_URL
_env = Path(__file__).resolve().parents[2] / "frontend" / ".env"
if _env.exists():
    load_dotenv(_env)

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or ""
).rstrip("/")

if not BASE_URL:
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL / EXPO_BACKEND_URL must be set")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _register(api_client, name):
    r = api_client.post(f"{BASE_URL}/api/family/register", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def three_users(api_client):
    """Register three users: parent, kid1, kid2."""
    parent = _register(api_client, "TEST_Parent")
    kid1 = _register(api_client, "TEST_Kid1")
    kid2 = _register(api_client, "TEST_Kid2")
    return {"parent": parent, "kid1": kid1, "kid2": kid2}
