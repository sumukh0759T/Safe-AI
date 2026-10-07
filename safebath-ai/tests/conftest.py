import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture()
def tmp_db(tmp_path):
    from backend.db import init_db
    p = str(tmp_path / "events.db")
    init_db(p)
    return p
