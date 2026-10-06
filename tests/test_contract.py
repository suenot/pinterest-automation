from copy import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import ACCOUNT, Adapter, BOARD, CONTRACT, PIN, fake_session
from pinterest_automation.browser import accepted_contract, stable_fingerprint, validate_contract
from pinterest_automation.cli import main
from pinterest_automation.errors import PublishError
from pinterest_automation.media import copy_video, probe_video, read_text, validate_text
from pinterest_automation.service import normalized, publish, reconcile
from pinterest_automation.state import State, board_url, canonical, pin_url, profile_url


@pytest.mark.parametrize("flags,code", [([], 2), (["publish"], 2), (["--help"], 0), (["--version"], 0), (["publish", "--help"], 0), (["accounts", "list"], 0), (["publish", "--account", "brand", "--request-id", "r"], 2)])
def test_single_json(flags, code, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PINTEREST_PUBLISH_STATE_DIR", str(tmp_path / "state"))
    assert main(flags) == code
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    value = json.loads(output)
    assert value["platform"] == "pinterest"
    if flags and flags[0] == "publish" and code == 2:
        assert {"account", "request_id", "status", "post_url", "error"} <= set(value)


def test_exact_text_and_character_limits(arguments):
    Path(arguments.description_file).write_bytes(b"  Exact\r\nDescription #tag \n")
    assert read_text(arguments.title_file, arguments.description_file) == ("  Caribbean Awesome  ", "  Exact\r\nDescription #tag \n")
    validate_text("t" * 100, "d" * 800)
    for title, description in [("t" * 101, "d"), ("t", "d" * 801), ("  ", "d"), ("t", "\n")]:
        with pytest.raises(PublishError):
            validate_text(title, description)
    arguments.ai_generated_person, arguments.ai_modified = "yes", "no"
    with pytest.raises(PublishError) as error:
        normalized(arguments)
    assert error.value.code == "INVALID_AI_FLAGS"


@pytest.mark.parametrize("url", ["http://pinterest.com/u/b/", "https://pin.it/x", "https://www.pinterest.com/u/b/?x=1", "https://www.pinterest.com/u/b/#x", "https://www.pinterest.com/u/b/c", "https://www.pinterest.com/pin/123/", "https://www.pinterest.com/u/b%2fc/"])
def test_board_rejects_external_or_ambiguous_urls(url):
    with pytest.raises(PublishError):
        board_url(url)


def test_binding_collision_and_transition_guard(state):
    with pytest.raises(PublishError):
        state.bind("second-alias", ACCOUNT)
    changed = {**ACCOUNT, "profile_url": "https://www.pinterest.com/other/", "username": "other"}
    with pytest.raises(PublishError):
        state.bind("brand", changed)
    state.start("brand", "r", {}, "fp", {})
    with pytest.raises(PublishError):
        state.finish("brand", "r", "published")
    assert profile_url("https://pinterest.com/CaribbeanAwesome") == ACCOUNT["profile_url"]
    assert pin_url(PIN.rstrip("/")) == PIN


def test_interprocess_lock_and_permissions(state):
    script = """from pathlib import Path
from pinterest_automation.state import State
from pinterest_automation.errors import PublishError
import sys
try:
    with State(Path(sys.argv[1])).lock('brand'): pass
except PublishError as exc:
    raise SystemExit(exc.exit_code)
"""
    with state.lock("brand"):
        process = subprocess.run([sys.executable, "-c", script, str(state.root)], capture_output=True)
    assert process.returncode == 3
    assert state.root.stat().st_mode & 0o777 == 0o700
    assert state.db_path.stat().st_mode & 0o777 == 0o600


def test_media_snapshot_and_real_probe(video, tmp_path):
    source = tmp_path / "source.m4v"
    source.write_bytes(video.read_bytes())
    target = tmp_path / "upload.m4v"
    digest = copy_video(str(source), target)
    original = target.read_bytes()
    source.write_bytes(b"changed")
    assert target.read_bytes() == original and len(digest) == 64
    assert target.stat().st_mode & 0o777 == 0o600
    probe_video(target)
    target.write_bytes(b"not mp4")
    with pytest.raises(PublishError):
        probe_video(target)
    fifo = tmp_path / "fifo.mp4"
    os.mkfifo(fifo)
    with pytest.raises(PublishError):
        copy_video(str(fifo), tmp_path / "fifo-copy.mp4")


@pytest.mark.parametrize("duration,codec,container,valid", [(4, "h264", "mov,mp4,m4a,3gp,3g2,mj2", True), (300, "hevc", "mov,mp4", True), (3.999, "h264", "mov", False), (300.001, "h264", "mov", False), (4, "vp9", "mov", False), (4, "h264", "matroska", False)])
def test_video_boundaries(video, monkeypatch, duration, codec, container, valid):
    import pinterest_automation.media as media
    document = {"streams": [{"codec_type": "video", "codec_name": codec, "width": 90, "height": 160}], "format": {"duration": str(duration), "format_name": container, "tags": {"major_brand": "isom"}}}
    monkeypatch.setattr(media.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(document).encode()))
    if valid:
        probe_video(video)
    else:
        with pytest.raises(PublishError):
            probe_video(video)


@pytest.mark.parametrize("fail,expected", [("verify", 3), ("account", 2), ("submit", 4), ("confirm", 4), ("interrupt", 4), (None, 0)])
async def test_durable_boundary_and_terminal_cache(state, arguments, fail, expected):
    Adapter.fail, Adapter.state = fail, state
    output, code = await publish(state, arguments, fake_session, Adapter)
    assert code == expected
    assert output["status"] == ("published" if expected == 0 else "uncertain" if expected == 4 else "failed")
    assert Adapter.clicks == (1 if expected in {0, 4} else 0)
    if expected in {0, 4}:
        # The source path stays the same; terminal outcomes must not read media.
        original = Path(arguments.video).read_bytes()
        Path(arguments.video).write_bytes(b"changed after submit")
        try:
            again, code = await publish(state, arguments, fake_session, Adapter)
            assert again == output and code == expected and Adapter.clicks == 1
        finally:
            Path(arguments.video).write_bytes(original)
    else:
        Adapter.fail = None
        output, code = await publish(state, arguments, fake_session, Adapter)
        assert code == 0 and Adapter.clicks == 1


async def test_source_changes_after_private_copy_and_retry_conflicts(state, arguments, tmp_path):
    source = tmp_path / "mutable.mp4"
    source.write_bytes(Path(arguments.video).read_bytes())
    arguments.video = str(source)
    Adapter.mutate = staticmethod(lambda: source.write_bytes(b"mutated source"))
    Adapter.fail = "verify"
    output, code = await publish(state, arguments, fake_session, Adapter)
    assert code == 3 and Adapter.clicks == 0
    Adapter.mutate = None
    with pytest.raises(PublishError):
        await publish(state, arguments, fake_session, Adapter)
    Path(arguments.title_file).write_text("Different")
    with pytest.raises(PublishError) as error:
        await publish(state, arguments, fake_session, Adapter)
    assert error.value.code == "REQUEST_CONFLICT"


async def test_ui_acceptance_required_and_stale_revision_rejected(state, arguments):
    with state.connect() as db:
        db.execute("UPDATE accounts SET ui_contract=NULL")
    output, code = await publish(state, arguments, fake_session, Adapter)
    assert code == 3 and output["error"]["code"] == "UI_NOT_VERIFIED" and Adapter.clicks == 0
    state.accept_ui("brand", {"adapter_revision": "old"})
    with pytest.raises(PublishError):
        accepted_contract(state.account("brand"))
    blank = {**CONTRACT, "selectors": {name: "" for name in CONTRACT["selectors"]}}
    with pytest.raises(PublishError):
        validate_contract(blank, ACCOUNT)


async def test_crash_recovery_read_only_status_and_manual_reconcile(state, arguments, capsys):
    details = normalized(arguments)
    state.start("brand", arguments.request_id, details, "fp", details)
    state.finish("brand", arguments.request_id, "submitting")
    assert main(["status", "--state-dir", str(state.root), "--account", "brand", "--request-id", arguments.request_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "uncertain"
    assert state.get("brand", arguments.request_id)["status"] == "submitting"
    output, code = await publish(state, arguments, fake_session, Adapter)
    assert code == 4 and Adapter.clicks == 0
    arguments.post_url, arguments.confirm_matching_pin = PIN, False
    with pytest.raises(PublishError):
        await reconcile(state, arguments, fake_session, Adapter)
    arguments.confirm_matching_pin = True
    output, code = await reconcile(state, arguments, fake_session, Adapter)
    assert code == 0 and output["post_url"] == PIN and Adapter.clicks == 0
    arguments.post_url = "https://www.pinterest.com/pin/999/"
    with pytest.raises(PublishError):
        await reconcile(state, arguments, fake_session, Adapter)


def test_unreadable_fingerprint_does_not_rotate(tmp_path):
    path = tmp_path / "fingerprint.json"
    path.write_text("invalid")
    with pytest.raises(PublishError) as error:
        stable_fingerprint(path)
    assert error.value.code == "FINGERPRINT_INVALID" and path.read_text() == "invalid"
    path.unlink()
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "prefs.js").write_text("existing profile")
    with pytest.raises(PublishError) as error:
        stable_fingerprint(path, profile)
    assert error.value.code == "FINGERPRINT_INVALID" and not path.exists()


@pytest.mark.parametrize("size,valid", [(200_000_000, True), (200_000_001, False), (0, False)])
def test_web_200mb_boundary_without_large_fixture(monkeypatch, size, valid):
    from types import SimpleNamespace
    import pinterest_automation.media as media
    fake = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=size))
    document = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 90, "height": 160}], "format": {"duration": "4", "format_name": "mov,mp4", "tags": {"major_brand": "isom"}}}
    monkeypatch.setattr(media.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(document).encode()))
    if valid:
        probe_video(fake)
    else:
        with pytest.raises(PublishError) as error:
            probe_video(fake)
        assert error.value.code == "INVALID_MEDIA"


def test_renamed_quicktime_bytes_are_not_mp4(video, tmp_path):
    quicktime = tmp_path / "quicktime.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-c", "copy", "-y", str(quicktime)], check=True, capture_output=True)
    renamed = tmp_path / "renamed.mp4"
    quicktime.rename(renamed)
    with pytest.raises(PublishError) as error:
        probe_video(renamed)
    assert error.value.code == "INVALID_MEDIA"


async def test_interrupt_immediately_after_boundary_commit_is_uncertain(state, arguments, monkeypatch):
    original = state.finish
    def finish(*args, **kwargs):
        original(*args, **kwargs)
        if args[2] == "submitting":
            raise KeyboardInterrupt()
    monkeypatch.setattr(state, "finish", finish)
    output, code = await publish(state, arguments, fake_session, Adapter)
    assert code == 4 and output["status"] == "uncertain" and Adapter.clicks == 0
    output, code = await publish(state, arguments, fake_session, Adapter)
    assert code == 4 and Adapter.clicks == 0


def test_growing_source_is_bounded_and_partial_snapshot_removed(tmp_path, monkeypatch):
    import pinterest_automation.media as media
    source = tmp_path / "source.mp4"
    source.write_bytes(b"123")
    target = tmp_path / "private.mp4"
    original = media.os.fstat
    def growing_source(fd):
        snapshot = original(fd)
        source.write_bytes(b"123456789")
        return snapshot
    monkeypatch.setattr(media, "MAX_VIDEO_BYTES", 4)
    monkeypatch.setattr(media.os, "fstat", growing_source)
    with pytest.raises(PublishError) as error:
        copy_video(str(source), target)
    assert error.value.code == "INVALID_MEDIA" and not target.exists()
    # O_EXCL failure never removes an already-owned destination.
    source.write_bytes(b"123")
    monkeypatch.setattr(media.os, "fstat", original)
    target.write_bytes(b"previous")
    with pytest.raises(PublishError):
        copy_video(str(source), target)
    assert target.read_bytes() == b"previous"


async def test_invalid_preflight_removes_snapshot_and_keeps_journal_untouched(state, arguments, monkeypatch):
    import pinterest_automation.service as service
    def invalid(_):
        raise PublishError("INVALID_MEDIA", "Invalid probe result")
    monkeypatch.setattr(service, "probe_video", invalid)
    with pytest.raises(PublishError):
        await publish(state, arguments, fake_session, Adapter)
    directory = state.request_dir(arguments.account, arguments.request_id)
    assert list(directory.glob("upload-*")) == []
    assert state.get(arguments.account, arguments.request_id) is None and Adapter.clicks == 0
