"""Controlled intercepted pages only; never contact a live Pinterest account."""
import base64
from contextlib import asynccontextmanager
from copy import deepcopy
from html import escape
import json
import os

import pytest

from conftest import ACCOUNT, BOARD, CONTRACT, PIN
from pinterest_automation.browser import PinterestEditor, browser_session
from pinterest_automation.errors import PublishError
from pinterest_automation.service import accept_ui, normalized, prepared, publish

pytestmark = pytest.mark.skipif(not os.environ.get("CAMOUFOX_EXECUTABLE_PATH"), reason="Controlled Camoufox checks need the paired explicit browser executable")


class Fixture:
    def __init__(self, video, arguments):
        self.video = video
        self.details = normalized(arguments)
        self.actor = ACCOUNT["profile_url"]
        self.board_actor = None
        self.owner = ACCOUNT["profile_url"]
        self.board_visibility = arguments.visibility.title()
        self.profile_visibility = "Public profile"
        self.collaboration = "Owner only"
        self.immediate = "Publish now"
        self.success_link = True
        self.preexisting_result = False
        self.preexisting_result_href = PIN
        self.result_href = PIN
        self.post_ready = "Published"
        self.post_board = BOARD
        self.clicks = 0
        self.http_requests = []

    def identity(self, actor=None):
        return f'<a id=current_actor_link href="{actor or self.actor}">Caribbean Awesome</a><span id=profile_visibility>{self.profile_visibility}</span>'

    def editor(self):
        result = f'<a id=result_link href="{self.result_href}">View Pin</a>' if self.success_link else ''
        callback = f"document.querySelector('#success').innerHTML={json.dumps(result)};document.querySelector('#success').hidden=false"
        initial = f'<a id=result_link href="{self.preexisting_result_href}">Old Pin</a>' if self.preexisting_result else ''
        return self.identity() + f'''<div id=editor><a id=composer_actor_link href="{self.actor}">Caribbean Awesome</a>
        <input id=upload type=file hidden onchange="document.querySelector('#upload_ready').hidden=false">
        <span id=upload_ready hidden>Ready</span><textarea id=title></textarea><textarea id=description></textarea><input id=link>
        <button id=board_menu onclick="document.querySelector('#board_option').hidden=false">Choose board</button>
        <a id=board_option hidden href="{BOARD}" onclick="event.preventDefault();document.querySelector('#selected_board_link').href=this.href;this.hidden=true">Island life</a>
        <a id=selected_board_link href="{BOARD}">Island life</a>
        <input id=ai_modified type=checkbox><input id=ai_generated_person type=checkbox>
        <span id=publication_timing>{self.immediate}</span>
        <button id=publish_button onclick="fetch('/fixture-publish',{{method:'POST'}}).then(()=>{{{escape(callback, quote=True)}}})">Publish</button>
        </div><a href=https://www.pinterest.com/pin/999/>Unrelated concurrent Pin</a><div id=success hidden>{initial}</div>'''

    def post(self):
        source = "data:video/mp4;base64," + base64.b64encode(self.video.read_bytes()).decode()
        return f'''<article id=post><a id=post_actor_link href="{self.actor}">Caribbean Awesome</a>
        <a id=post_board_link href="{self.post_board}">Island life</a>
        <h1 id=post_title>{escape(self.details['title'])}</h1><pre id=post_description>{escape(self.details['description'])}</pre>
        <video id=post_video src="{source}" preload=auto controls></video>
        <span id=post_status>{self.post_ready}</span><span id=post_visibility>{self.board_visibility}</span></article>'''

    async def route(self, route):
        url = route.request.url
        self.http_requests.append(url)
        if url == "https://www.pinterest.com/fixture-publish":
            self.clicks += 1
            await route.fulfill(status=200, content_type="application/json", body="{}")
            return
        elif url == CONTRACT["editor_url"]:
            body = self.editor()
        elif url == BOARD:
            body = self.identity(self.board_actor) + f'<a id=board_owner_link href="{self.owner}">Caribbean Awesome</a><span id=board_visibility>{self.board_visibility}</span><span id=board_collaboration>{self.collaboration}</span>'
        elif url == ACCOUNT["profile_url"]:
            body = self.identity()
        elif url == PIN:
            body = self.post()
        else:
            await route.abort()
            return
        await route.fulfill(status=200, content_type="text/html", body=body)


@pytest.fixture(autouse=True)
def quick_hands(monkeypatch):
    """Keep the input path these checks exercise, without its pauses.

    The publisher enters text at a person's pace, which is minutes over a
    request here and a 10-second controlled check cannot hold it. What the
    pace itself must look like is `test_human`'s subject; what these checks
    watch is which events the page receives and what the publisher does with
    them, so the intervals are compressed and the keystrokes are not.
    """
    from pinterest_automation import human
    monkeypatch.setattr(human, "KEY_LOGNORM", (0.0, 0.05))
    monkeypatch.setattr(human, "KEY_MINIMUM_MS", 1)
    monkeypatch.setattr(human, "MOVE_STEPS", (2, 3))
    monkeypatch.setattr(human, "PASTE_READ_MS", 10)
    for name in ("WORD_BREAK_MS", "SENTENCE_BREAK_MS", "LINE_BREAK_MS", "SHIFT_MS",
                 "THINK_MS", "TYPO_NOTICE_MS", "TYPO_RECOVER_MS", "MOVE_STEP_MS",
                 "PRESS_MS", "AIM_MS", "SETTLE_MS"):
        monkeypatch.setattr(human, name, (0, 1))


@pytest.fixture
async def browser(tmp_path, arguments, video, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    fixture = Fixture(video, arguments)
    async with browser_session(profile, True) as context:
        await context.route("**/*", fixture.route)
        # Every new public context is intercepted before it can navigate.
        original = context.browser.new_context
        async def clean_context(**options):
            fresh = await original(**options)
            await fresh.route("**/*", fixture.route)
            return fresh
        monkeypatch.setattr(context.browser, "new_context", clean_context)
        @asynccontextmanager
        async def session(*_):
            yield context
        yield context, fixture, session


async def adapter_ready(browser, tmp_path, arguments):
    context, fixture, session = browser
    adapter = PinterestEditor(context, deepcopy(CONTRACT), tmp_path / "diagnostics")
    directory = tmp_path / "copy"
    directory.mkdir(mode=0o700)
    video, details, _ = prepared(normalized(arguments), directory)
    await adapter.open_editor(ACCOUNT)
    await adapter.prepare(video, details)
    return adapter, details


async def test_visible_preflight_makes_zero_clicks_then_full_one_click_flow(browser, state, arguments, tmp_path):
    context, fixture, session = browser
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(CONTRACT))
    arguments.ui_contract = str(path)
    output, code = await accept_ui(state, arguments, session)
    assert code == 0 and fixture.clicks == 0 and output["status"] == "ui_accepted"
    output, code = await publish(state, arguments, session)
    assert code == 0 and output["post_url"] == PIN and fixture.clicks == 1, (code, output, fixture.clicks)
    output, code = await publish(state, arguments, session)
    assert code == 0 and fixture.clicks == 1
    assert all(url.startswith("https://www.pinterest.com/") for url in fixture.http_requests)


@pytest.mark.parametrize("attribute,value,code", [("actor", "https://www.pinterest.com/other/", "ACCOUNT_MISMATCH"), ("owner", "https://www.pinterest.com/other/", "ACCOUNT_MISMATCH"), ("board_visibility", "Public", "VISIBILITY_MISMATCH"), ("collaboration", "Shared board", "COLLABORATIVE_BOARD_UNSUPPORTED"), ("immediate", "Publish later", "SCHEDULING_UNSUPPORTED")])
async def test_mismatches_never_publish(browser, state, arguments, attribute, value, code):
    context, fixture, session = browser
    setattr(fixture, attribute, value)
    output, exit_code = await publish(state, arguments, session)
    assert exit_code == 2 and output["error"]["code"] == code and fixture.clicks == 0


async def test_exact_caption_board_ai_and_upload_rechecked(browser, tmp_path, arguments):
    adapter, details = await adapter_ready(browser, tmp_path, arguments)
    await adapter.verify(ACCOUNT, details)
    await adapter.page.locator("#description").fill("Wrong caption")
    with pytest.raises(PublishError) as error:
        await adapter.verify(ACCOUNT, details)
    assert error.value.code == "TEXT_MISMATCH"
    await adapter.page.locator("#description").fill(details["description"])
    await adapter.page.locator("#selected_board_link").evaluate("element=>element.href='https://www.pinterest.com/caribbeanawesome/other/'")
    with pytest.raises(PublishError) as error:
        await adapter.verify(ACCOUNT, details)
    assert error.value.code == "BOARD_MISMATCH"
    await adapter.page.locator("#selected_board_link").evaluate("(element,url)=>element.href=url", BOARD)
    await adapter.page.locator("#ai_modified").uncheck()
    with pytest.raises(PublishError) as error:
        await adapter.verify(ACCOUNT, details)
    assert error.value.code == "AI_STATE_MISMATCH"
    await adapter.page.locator("#ai_modified").check()
    await adapter.page.locator("#upload").set_input_files([])
    with pytest.raises(PublishError) as error:
        await adapter.verify(ACCOUNT, details)
    assert error.value.code == "UPLOAD_UNCONFIRMED"
    assert browser[1].clicks == 0


async def test_ai_unreadable_and_prep_selector_cannot_click_publish(browser, tmp_path, arguments):
    adapter, details = await adapter_ready(browser, tmp_path, arguments)
    adapter.contract["selectors"]["board_menu"] = "#publish_button"
    with pytest.raises(PublishError) as error:
        await adapter.set_board(details, await adapter.control("editor"))
    assert error.value.code == "UNSAFE_PRE_SUBMIT_CONTROL" and browser[1].clicks == 0
    await adapter.page.locator("#ai_modified").evaluate("element=>element.type='button'")
    with pytest.raises(PublishError) as error:
        await adapter.ai_state(adapter.page.locator("#ai_modified"))
    assert error.value.code == "AI_STATE_UNVERIFIED"


@pytest.mark.parametrize("attribute,value", [("success_link", False), ("post_ready", "Processing"), ("post_board", "https://www.pinterest.com/caribbeanawesome/other/")])
async def test_uncertain_outcome_keeps_one_click(browser, state, arguments, attribute, value):
    context, fixture, session = browser
    setattr(fixture, attribute, value)
    arguments.timeout_seconds = 15
    output, code = await publish(state, arguments, session)
    assert code == 4 and output["status"] == "uncertain" and fixture.clicks == 1
    again, code = await publish(state, arguments, session)
    assert code == 4 and again == output and fixture.clicks == 1
    if attribute == "success_link":
        assert output["post_url"] is None
    else:
        assert output["post_url"] == PIN


async def test_public_requires_public_profile_and_clean_context(browser, state, arguments):
    context, fixture, session = browser
    arguments.visibility = "public"
    fixture.details = normalized(arguments)
    fixture.board_visibility = "Public"
    fixture.profile_visibility = "Private profile"
    output, code = await publish(state, arguments, session)
    assert code == 2 and fixture.clicks == 0
    fixture.profile_visibility = "Public profile"
    fixture.http_requests.clear()
    output, code = await publish(state, arguments, session)
    assert code == 0 and fixture.clicks == 1 and fixture.http_requests.count(PIN) == 2


async def test_stale_result_and_second_publish_are_rejected(browser, tmp_path, arguments):
    adapter, details = await adapter_ready(browser, tmp_path, arguments)
    await adapter.page.locator("#success").evaluate("element=>{element.textContent='Previous Pin published';element.hidden=false}")
    with pytest.raises(PublishError) as error:
        await adapter.verify(ACCOUNT, details)
    assert error.value.code == "STALE_SUCCESS_SURFACE" and browser[1].clicks == 0
    await adapter.page.locator("#success").evaluate("element=>{element.hidden=true;element.textContent=''}")
    await adapter.verify(ACCOUNT, details)
    await adapter.submit()
    await adapter.control("success")
    assert browser[1].clicks == 1
    with pytest.raises(PublishError) as error:
        await adapter.submit()
    assert error.value.code == "DUPLICATE_SUBMIT" and browser[1].clicks == 1


async def test_associated_labels_and_secondary_submit_cannot_publish_during_prepare(browser, tmp_path, arguments):
    adapter, details = await adapter_ready(browser, tmp_path, arguments)
    editor = await adapter.control("editor")
    cases = [
        '<label id=unsafe for=publish_button>Choose board</label>',
        '<label for=publish_button><span id=unsafe>Choose board</span></label>',
        '<div id=unsafe><label for=publish_button>Choose board</label></div>',
        '<form id=unsafe_form></form><input id=unsafe type=submit form=unsafe_form>',
    ]
    adapter.contract["selectors"]["board_menu"] = "#unsafe"
    for html in cases:
        await adapter.page.locator("#editor").evaluate("(element, html)=>element.insertAdjacentHTML('beforeend', html)", html)
        await adapter.page.locator("#publish_button").evaluate("element=>element.setAttribute('form','unsafe_form')")
        with pytest.raises(PublishError) as error:
            await adapter.set_board(details, editor)
        assert error.value.code == "UNSAFE_PRE_SUBMIT_CONTROL" and browser[1].clicks == 0
        await adapter.page.locator("#unsafe").evaluate("element=>element.closest('label')?.remove() || element.remove()")
    assert browser[1].clicks == 0


@pytest.mark.parametrize("prior_href", [PIN, PIN + "?tracking=1", PIN + "#fragment"])
async def test_hidden_old_success_link_cannot_confirm_current_publish(browser, state, arguments, prior_href):
    context, fixture, session = browser
    fixture.preexisting_result = True
    fixture.preexisting_result_href = prior_href
    output, code = await publish(state, arguments, session)
    assert code == 4 and output["status"] == "uncertain" and output["post_url"] is None
    assert output["error"]["code"] == "STALE_RESULT_URL" and fixture.clicks == 1
    again, code = await publish(state, arguments, session)
    assert code == 4 and again == output and fixture.clicks == 1


async def test_new_tracked_result_is_canonicalized(browser, state, arguments):
    context, fixture, session = browser
    fixture.result_href = PIN + "?share=1#fragment"
    output, code = await publish(state, arguments, session)
    assert code == 0 and output["post_url"] == PIN and fixture.clicks == 1


async def test_relative_actor_uses_owner_document_and_empty_href_fails(browser, tmp_path, arguments):
    context, fixture, session = browser
    fixture.board_actor = fixture.owner = "../"
    adapter, details = await adapter_ready(browser, tmp_path, arguments)
    await adapter.verify(ACCOUNT, details)
    fixture.owner = "../caribbeanawesome/"
    with pytest.raises(PublishError) as error:
        await adapter.verify(ACCOUNT, details)
    assert error.value.code == "LOGIN_REQUIRED" and fixture.clicks == 0
    await adapter.identity_page(ACCOUNT)
    for value in (None, ""):
        if value is None:
            await adapter.page.locator("#current_actor_link").evaluate("element=>element.removeAttribute('href')")
        else:
            await adapter.page.locator("#current_actor_link").evaluate("element=>element.setAttribute('href','')")
        with pytest.raises(PublishError) as error:
            await adapter.verify_identity(ACCOUNT)
        assert error.value.code == "LOGIN_REQUIRED" and fixture.clicks == 0


async def test_guest_pin_uses_readability_and_ignores_description_ui(browser, tmp_path, arguments):
    context, fixture, session = browser
    adapter, details = await adapter_ready(browser, tmp_path, arguments)
    details["visibility"] = "public"
    original = fixture.post

    def public_post():
        return original().replace('id=post_board_link', 'id=post_board_link hidden').replace(
            f'<span id=post_status>{fixture.post_ready}</span><span id=post_visibility>{fixture.board_visibility}</span>', '').replace(
            '</pre>', '<style>.irrelevant{color:red}</style><span data-test-id="more-description-container"> ... more</span></pre>')

    fixture.post = public_post
    await adapter.verify_public(PIN, ACCOUNT, details)
    assert fixture.clicks == 0


async def test_live_rehearsal_rejects_publish_and_unbound_pin_navigation(browser, tmp_path, arguments):
    from pinterest_automation.preflight import PinterestPreflight
    context, fixture, session = browser
    contract = deepcopy(CONTRACT)
    contract["scope"] = "publish"  # Even an unvalidated injected scope cannot activate this adapter.
    adapter = PinterestPreflight(context, contract, tmp_path / "diagnostics")
    adapter.page = await context.new_page()
    await adapter.page.goto(CONTRACT["editor_url"])
    details = normalized(arguments)
    adapter.prepared_request = (ACCOUNT, details)
    with pytest.raises(PublishError) as error:
        await adapter.submit()
    assert error.value.code == "UI_NOT_VERIFIED" and fixture.clicks == 0

    await adapter.page.goto(PIN)
    with pytest.raises(PublishError) as error:
        await adapter.confirm(ACCOUNT, details)
    assert error.value.code == "UNCONFIRMED_POST" and adapter.post_url is None and fixture.clicks == 0
