from collections import defaultdict

import pytest

from server import security


@pytest.fixture(autouse=True)
def _fresh_throttle(monkeypatch):
    """Every test client comes from the same address, so attempt counters
    would otherwise leak from one test into the next."""
    monkeypatch.setattr(security, "_attempts", defaultdict(list))
