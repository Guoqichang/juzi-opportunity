import pytest

from compute_policy.demo import seed_demo
from compute_policy.service import Service
from compute_policy.store import Store


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.delenv("POLICY_DATABASE_URL", raising=False)
    for name in ("POLICY_MODEL_BASE_URL", "POLICY_MODEL_API_KEY", "POLICY_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)
    return Service(Store(tmp_path))


@pytest.fixture
def demo(service):
    seed_demo(service)
    return service
