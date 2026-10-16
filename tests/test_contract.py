from copy import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import ACCOUNT, Adapter, BOARD, CONTRACT, PIN, fake_session
from pinterest_automation.browser import ADAPTER_REVISION, BROWSER_BUILD, RUNTIME, accepted_contract, stable_fingerprint, validate_contract
from pinterest_automation.cli import main
from pinterest_automation.errors import PublishError
from pinterest_automation.media import copy_video, probe_video, read_text, validate_text
from pinterest_automation.service import accept_ui, normalized, publish, reconcile
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


@pytest.mark.parametrize("brand,valid", [("isom", True), ("isom;isom", True), ("mp42;mp42;mp42", True),
                                         ("iso6", True), ("qt  ", False), ("qt  ;qt  ", False), ("", False)])
def test_repeated_container_brand_tags_are_read_as_one_brand(video, monkeypatch, brand, valid):
    """ffprobe joins repeated tags, and some muxers write major_brand twice."""
    import pinterest_automation.media as media
    document = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 90, "height": 160}],
                "format": {"duration": "32.7", "format_name": "mov,mp4,m4a,3gp,3g2,mj2", "tags": {"major_brand": brand}}}
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


def test_pre_submit_contract_never_enables_publication(state):
    from pinterest_automation.browser import load_contract
    path = str(Path(__file__).parents[1] / "examples/caribbeanawesome.pre-submit.json")
    contract = load_contract(path, ACCOUNT, "pre-submit")
    assert load_contract(path, ACCOUNT, "login") == contract
    state.accept_ui("brand", {"adapter_revision": ADAPTER_REVISION,
        "runtime": RUNTIME,
        "browser_build": BROWSER_BUILD,
        "evidence_sha256": "pre-submit", "contract": contract})
    with pytest.raises(PublishError) as error:
        accepted_contract(state.account("brand"))
    assert error.value.code == "UI_NOT_VERIFIED"
    from pinterest_automation.browser import validate_contract
    contract["scope"] = "publish"
    with pytest.raises(PublishError) as error:
        validate_contract(contract, ACCOUNT, "pre-submit")
    assert error.value.code == "INVALID_UI_CONTRACT"


def test_live_contract_requires_recorded_selected_draft_and_board_id():
    from pinterest_automation.browser import load_contract
    path = str(Path(__file__).parents[1] / "examples/caribbeanawesome.publish.json")
    contract = load_contract(path, ACCOUNT, "publish")
    assert contract["board"]["id"] == "1085297278893878117"
    del contract["selectors"]["selected_draft"]
    with pytest.raises(PublishError) as error:
        validate_contract(contract, ACCOUNT, "publish")
    assert error.value.code == "INVALID_UI_CONTRACT"


async def test_live_acceptance_discards_its_fresh_draft_before_recording(state, arguments, tmp_path):
    arguments.ui_contract = str(Path(__file__).parents[1] / "examples/caribbeanawesome.publish.json")
    previous = state.account("brand")["ui_contract"]
    class Rehearsal:
        discarded = False
        def __init__(self, *_):
            pass
        async def open_editor(self, *_):
            pass
        async def prepare(self, *_):
            pass
        async def verify(self, *_):
            pass
        async def screenshot(self, *_):
            path = tmp_path / "accepted.png"
            path.write_bytes(b"evidence")
            return path
        async def discard_rehearsal_draft(self):
            assert state.account("brand")["ui_contract"] == previous
            self.discarded = True
    instance = None
    def make(*args):
        nonlocal instance
        instance = Rehearsal(*args)
        return instance
    output, code = await accept_ui(state, arguments, fake_session, make)
    assert code == 0 and output["status"] == "ui_accepted" and instance.discarded
    assert state.account("brand")["ui_contract"] != previous


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_failed_live_acceptance_cleans_only_its_bound_fresh_draft(state, arguments, cleanup_fails):
    arguments.ui_contract = str(Path(__file__).parents[1] / "examples/caribbeanawesome.publish.json")
    previous = state.account("brand")["ui_contract"]
    class Rehearsal:
        draft_id = None
        submitted = False
        cleanup_calls = 0
        def __init__(self, *_):
            pass
        async def open_editor(self, *_):
            pass
        async def prepare(self, *_):
            self.draft_id = "12345"  # Fresh ID bound during this invocation.
        async def verify(self, *_):
            raise PublishError("TEXT_MISMATCH", "Original preflight failure", 3)
        async def screenshot(self, *_):
            return None
        async def discard_rehearsal_draft(self):
            self.cleanup_calls += 1
            if cleanup_fails:
                raise PublishError("DRAFT_CLEANUP_UNVERIFIED", "Deletion response missing", 3)
    instance = None
    def make(*args):
        nonlocal instance
        instance = Rehearsal(*args)
        return instance
    with pytest.raises(PublishError) as failure:
        await accept_ui(state, arguments, fake_session, make)
    assert failure.value.code == "TEXT_MISMATCH" and instance.cleanup_calls == 1
    assert state.account("brand")["ui_contract"] == previous
    assert ("12345" in failure.value.message) == cleanup_fails
    if cleanup_fails:
        assert "DRAFT_CLEANUP_UNVERIFIED" in failure.value.message


async def test_live_creation_result_requires_unique_create_and_active_draft_delete(tmp_path):
    from pinterest_automation.browser import load_contract
    from pinterest_automation.preflight import PinterestLivePublisher
    class Context:
        def set_default_timeout(self, _):
            pass
    class Response:
        status = 200
        def __init__(self, verb, path, data, request_data):
            self.url = f"https://www.pinterest.com/resource/ApiResource/{verb}/"
            self.request = type('Request', (), {'method': 'POST', 'post_data': request_data})()
            self.path, self.data = path, data
        async def json(self):
            return {"resource": {"options": {"url": self.path}}, "resource_response": {"status": "success", "data": self.data}}
    contract = load_contract(str(Path(__file__).parents[1] / "examples/caribbeanawesome.publish.json"), ACCOUNT, "publish")
    adapter = PinterestLivePublisher(Context(), contract, tmp_path / "diagnostics")
    adapter.draft_id, adapter.inner_draft_id = "12345", "12346"
    create = Response('create', '/v3/storypins/', {'id': '98765', 'story_pin_data_id': '67890'}, '{}')
    wrong_delete = Response('delete', '/v3/storypins/drafts/another/', {}, '{"draft_id":"another"}')
    own_delete = Response('delete', '/v3/storypins/drafts/12345/', {}, '{"draft_id":"12345"}')
    await adapter.capture_publish(create)
    await adapter.capture_publish(wrong_delete)
    assert adapter.publish_urls == set()
    await adapter.capture_publish(own_delete)
    assert adapter.publish_urls == {"https://www.pinterest.com/pin/98765/"}


def test_saved_editor_text_requires_save_after_edit_and_get_issued_after_save():
    from pinterest_automation.preflight import EditorEvidence
    evidence = EditorEvidence()
    save = {"resource_response": {"status": "success"}, "resource": {"options": {"url": "/v3/storypins/drafts/123/"}}}
    draft = {"id": "123", "type": "storypindata", "draft": {"pin_description": "Exact\ntext"}, "metadata": {"pin_title": "Title"}}
    get = {"resource_response": {"status": "success", "data": [draft]}, "resource": {"options": {"url": "/v3/users/456/storypins/drafts/"}}}
    evidence.record("/resource/ApiResource/update/", save, issued=2, started=200, finished=400)
    evidence.record("/resource/ApiResource/get/", get, issued=3, started=300, finished=500)
    assert not evidence.field_saved("123", "title", "Title", after=1)  # Late response to an early GET.
    evidence.record("/resource/ApiResource/get/", get, issued=6, started=450, finished=700)
    assert evidence.field_saved("123", "title", "Title", after=1)
    assert not evidence.field_saved("123", "title", "Title", after=2)  # Save predates this edit.
    assert not evidence.field_saved("another", "title", "Title", after=1)
    assert evidence.field_saved("123", "description", "Exact\ntext", after=1)
    assert not evidence.field_saved("123", "description", "Exact text", after=1)
    assert evidence.field_saved("123", "link", "")  # Unchanged empty field reuses the fresh snapshot.
    changed = {**get, "resource_response": {"status": "success", "data": []}}
    evidence.record("/resource/ApiResource/get/", changed, issued=3, started=300, finished=800)
    assert evidence.field_saved("123", "title", "Title", after=1)  # Older-issued responses cannot replace a newer snapshot.
