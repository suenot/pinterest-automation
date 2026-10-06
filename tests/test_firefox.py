import sqlite3
import time

import pytest

from pinterest_automation.errors import PublishError
from pinterest_automation.firefox import pinterest_cookies


def database(tmp_path, rows):
    path = tmp_path / "cookies.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE moz_cookies(host,name,value,path,expiry,isSecure,isHttpOnly,sameSite,originAttributes,isPartitionedAttributeSet)")
        connection.executemany("INSERT INTO moz_cookies VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    return path


def test_import_preserves_scope_and_skips_containers_partitions_and_expired(tmp_path):
    future = int(time.time()) + 3600
    path = database(tmp_path, [
        (".pinterest.com", "_pinterest_sess", "private", "/", future * 1000, 1, 1, 0, "", 0),
        ("www.pinterest.com", "csrftoken", "private", "/pin/", future, 1, 0, 1, "", 0),
        (".pinterest.com", "container", "private", "/", future, 1, 1, 0, "^userContextId=1", 0),
        (".pinterest.com", "partition", "private", "/", future, 1, 1, 0, "^partitionKey=site", 0),
        (".pinterest.com", "partition-flag", "private", "/", future, 1, 1, 0, "", 1),
        (".pinterest.com", "expired", "private", "/", 1, 1, 1, 0, "", 0),
        ("unrelated.example", "other", "private", "/", future, 1, 1, 0, "", 0),
    ])
    before = path.read_bytes()
    cookies = pinterest_cookies(str(tmp_path))
    assert [cookie["name"] for cookie in cookies] == ["_pinterest_sess", "csrftoken"]
    assert cookies[0]["expires"] == future
    assert cookies[0]["domain"] == ".pinterest.com"
    assert cookies[1]["domain"] == "www.pinterest.com" and cookies[1]["path"] == "/pin/"
    assert all("url" not in cookie for cookie in cookies)
    assert path.read_bytes() == before


def test_missing_session_and_profile_fail_without_browser(tmp_path):
    with pytest.raises(PublishError) as error:
        pinterest_cookies(str(tmp_path))
    assert error.value.code == "FIREFOX_PROFILE_INVALID"
    database(tmp_path, [])
    with pytest.raises(PublishError) as error:
        pinterest_cookies(str(tmp_path))
    assert error.value.code == "FIREFOX_SESSION_MISSING"


async def test_import_rolls_back_pinterest_cookies_when_account_mismatches(tmp_path, monkeypatch):
    from argparse import Namespace
    from contextlib import asynccontextmanager
    import json

    from conftest import ACCOUNT, CONTRACT
    from pinterest_automation import service
    from pinterest_automation.state import State

    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(CONTRACT))
    state = State(tmp_path / "state")
    original = [{"name": "_pinterest_sess", "value": "original", "domain": ".pinterest.com", "path": "/"}]
    imported = [{**original[0], "value": "imported"}]
    monkeypatch.setattr(service, "pinterest_cookies", lambda _: imported)

    class Page:
        async def goto(self, *_args, **_kwargs):
            pass

    class Context:
        saved = original.copy()
        async def cookies(self):
            return self.saved.copy()
        async def clear_cookies(self, **_kwargs):
            self.saved = []
        async def add_cookies(self, cookies):
            self.saved.extend(cookies)
        async def new_page(self):
            return Page()

    context = Context()
    @asynccontextmanager
    async def session(*_):
        yield context

    class WrongAccount:
        def __init__(self, *_):
            pass
        async def verify_identity(self, _):
            assert context.saved == imported
            raise PublishError("ACCOUNT_MISMATCH", "Different canonical account")
        async def screenshot(self):
            return None

    args = Namespace(account="brand", profile_url=ACCOUNT["profile_url"], display_name=ACCOUNT["display_name"],
                     ui_contract=str(contract), firefox_profile="source", timeout_seconds=5)
    with pytest.raises(PublishError) as error:
        await service.login(state, args, session, WrongAccount)
    assert error.value.code == "ACCOUNT_MISMATCH"
    assert context.saved == original and state.accounts() == []
