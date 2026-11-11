from fastapi.testclient import TestClient

from agdash import __version__
from agdash.config import Settings
from agdash.main import create_app


def test_health_reports_ok_and_version():
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_default_bind_address_is_loopback():
    assert Settings().host == "127.0.0.1"
