import datetime as dt
import os

import pytest

streamlit = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from footy.demo import build  # noqa: E402

PAGES = ["Data coverage", "League tables", "Team form", "Predictions & value", "Match explorer",
         "FPL planner", "Players & props", "Referees & cards", "Backtest"]


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("dash") / "demo.db"
    build(str(path), today=dt.date.today())
    return path


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(demo_db, page, monkeypatch):
    monkeypatch.setenv("FOOTY_DB", str(demo_db))
    at = AppTest.from_file(os.path.join(os.path.dirname(__file__), "..", "app", "dashboard.py"),
                           default_timeout=120)
    at.run()
    at.sidebar.radio[0].set_value(page).run()
    if page == "Backtest":
        at.button[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
