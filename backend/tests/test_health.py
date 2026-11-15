from fastapi.testclient import TestClient

from agdash import __version__
from agdash.config import Settings
from agdash.main import create_app


def test_health_reports_ok_and_version():
    client = TestClient(create_app(token="unused", port=8765), base_url="http://127.0.0.1:8765")
    response = client.get("/health")  # no token: health carries no data

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_default_bind_address_is_loopback():
    assert Settings().host == "127.0.0.1"
