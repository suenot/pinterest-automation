from argparse import Namespace
from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
import subprocess

import pytest

from pinterest_automation.browser import ADAPTER_REVISION, BROWSER_BUILD, RUNTIME, SELECTORS
from pinterest_automation.state import State

ACCOUNT = {"profile_url": "https://www.pinterest.com/caribbeanawesome/", "username": "caribbeanawesome", "display_name": "Caribbean Awesome"}
BOARD = "https://www.pinterest.com/caribbeanawesome/island-life/"
PIN = "https://www.pinterest.com/pin/123456789/"
CONTRACT = {"schema_version": "1.0.0", "target": ACCOUNT, "editor_url": "https://www.pinterest.com/pin-creation-tool/",
    "selectors": {name: "#" + name for name in SELECTORS},
    "labels": {"public_profile": "Public profile", "private_profile": "Private profile", "public_board": "Public", "private_board": "Private",
        "solo_board": "Owner only", "upload_ready": "Ready", "immediate": "Publish now", "published": "Published"}}


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=90x160:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(path)], check=True, capture_output=True)
    return path


@pytest.fixture
def arguments(tmp_path, video):
    title = tmp_path / "title.txt"
    description = tmp_path / "description.txt"
    title.write_bytes(b"  Caribbean Awesome  ")
    description.write_bytes(b"  Exact\nDescription #Caribbean \n")
    return Namespace(account="brand", request_id="request-1", video=str(video), title_file=str(title), description_file=str(description), metadata_file=None,
        board_url=BOARD, visibility="private", ai_modified="yes", ai_generated_person="no", link=None, headless=True, timeout_seconds=30)


@pytest.fixture
def state(tmp_path):
    state = State(tmp_path / "state")
    state.bind("brand", ACCOUNT)
    accept(state)
    return state


def accept(state):
    state.accept_ui("brand", {"adapter_revision": ADAPTER_REVISION, "runtime": RUNTIME, "browser_build": BROWSER_BUILD, "evidence_sha256": "controlled-fixture", "contract": CONTRACT})


@asynccontextmanager
async def fake_session(*_):
    yield object()


class Adapter:
    clicks = 0
    fail = None
    mutate = None
    state = None
    private_video = None
    post_url = None

    def __init__(self, *_):
        pass

    async def open_editor(self, *_):
        pass

    async def prepare(self, video, details):
        type(self).private_video = video
        if self.mutate:
            self.mutate()
        assert hashlib.sha256(video.read_bytes()).hexdigest() == details["video_sha256"]

    async def verify(self, *_):
        if self.fail == "verify":
            raise ValueError("changed UI")
        if self.fail == "account":
            from pinterest_automation.errors import PublishError
            raise PublishError("ACCOUNT_MISMATCH", "Wrong account")

    async def submit(self):
        type(self).clicks += 1
        if self.state:
            assert self.state.get("brand", "request-1")["status"] == "submitting"
        if self.fail == "submit":
            raise TimeoutError()
        if self.fail == "interrupt":
            raise KeyboardInterrupt()

    async def confirm(self, *_):
        if self.fail == "confirm":
            self.post_url = PIN
            raise TimeoutError()
        return PIN

    async def screenshot(self, *_):
        return None

    async def identity_page(self, *_):
        pass

    async def verify_post(self, *_):
        if self.fail == "reconcile":
            raise TimeoutError()

    async def verify_public(self, *_):
        pass


@pytest.fixture(autouse=True)
def reset_adapter():
    Adapter.clicks, Adapter.fail, Adapter.mutate, Adapter.state = 0, None, None, None
