"""Recorded Pinterest web adapter with a separate pre-submit-only contract."""

import asyncio
import re
import sys
from urllib.parse import urlsplit

from .browser import PinterestEditor, authenticated_url
from .errors import PublishError
from .state import board_url, pinterest_url

SELECTORS = {"current_actor_link", "editor", "upload", "upload_ready", "title", "description", "link", "board_menu", "board_option",
             "selected_board_label", "ai_modified", "ai_generated_person", "publish_button", "board_title", "board_visibility", "board_collaboration"}
POST_SELECTORS = {"post", "post_actor_link", "post_board_link", "post_title", "post_description", "post_link", "post_video"}


class EditorEvidence:
    """Saved text from the current page's ordinary UI draft requests."""
    def __init__(self):
        self.drafts, self.saves = {}, {}
        self.drafts_loaded = False
        self.drafts_sequence = -1
        self.drafts_started = -1

    def record(self, path, payload, issued, started, finished):
        response = payload.get("resource_response", {})
        if response.get("status") != "success":
            return
        data = response.get("data")
        options = payload.get("resource", {}).get("options", {})
        if path == "/resource/ApiResource/get/" and re.fullmatch(r"/v3/users/[0-9]+/storypins/drafts/", options.get("url", "")) and isinstance(data, list):
            if issued > self.drafts_sequence:
                self.drafts = {entry["id"]: entry for entry in data if isinstance(entry, dict) and entry.get("type") == "storypindata" and isinstance(entry.get("id"), str)}
                self.drafts_sequence, self.drafts_loaded = issued, True
                self.drafts_started = started
        elif path == "/resource/ApiResource/update/":
            match = re.fullmatch(r"/v3/storypins/drafts/([0-9]+)/", options.get("url", ""))
            if match:
                previous = self.saves.get(match.group(1), (-1, -1))
                if issued > previous[0]:
                    self.saves[match.group(1)] = (issued, finished)

    def field_saved(self, draft_id, name, value, after=0):
        entry = self.drafts.get(draft_id)
        saved = self.saves.get(draft_id)
        if not entry or not saved or saved[0] <= after or self.drafts_started <= saved[1]:
            return False
        draft, metadata = entry.get("draft", {}), entry.get("metadata", {})
        actual = metadata.get("pin_title") if name == "title" else draft.get("pin_description" if name == "description" else "link")
        return (actual or "") == value


def validate_preflight_contract(value, expected, stage):
    if set(value) != {"schema_version", "scope", "target", "editor_url", "board", "selectors"} or value["scope"] != "pre-submit":
        raise PublishError("INVALID_UI_CONTRACT", "Schema 1.1.0 supports only a recorded pre-submit rehearsal.")
    if value["target"] != {name: expected[name] for name in ("profile_url", "username", "display_name")}:
        raise PublishError("ACCOUNT_MISMATCH", "Recorded editor contract target differs from the bound Pinterest profile.")
    selectors = value["selectors"]
    if not isinstance(selectors, dict) or set(selectors) not in (SELECTORS, SELECTORS | POST_SELECTORS) or any(not isinstance(item, str) or not item.strip() for item in selectors.values()):
        raise PublishError("INVALID_UI_CONTRACT", "Record every pre-submit control selector.")
    board = value["board"]
    if not isinstance(board, dict) or set(board) != {"url", "name"} or not isinstance(board["name"], str) or not board["name"].strip():
        raise PublishError("INVALID_UI_CONTRACT", "Record the exact board URL and name for this rehearsal.")
    if board_url(board["url"]) != board["url"] or board["url"].split("/")[3] != expected["username"]:
        raise PublishError("BOARD_OWNERSHIP_MISMATCH", "Rehearsal board must belong to the bound canonical username.")
    pinterest_url(value["editor_url"])
    if stage not in {"login", "pre-submit"}:
        raise PublishError("UI_NOT_VERIFIED", "Recorded live editor acceptance is pre-submit-only: active draft, final board/timing and this Publish's causal result remain unverified.", 3)
    return value


class PinterestPreflight(PinterestEditor):
    def __init__(self, context, contract, diagnostics):
        super().__init__(context, contract, diagnostics)
        context.set_default_timeout(60000)
        self.evidence = EditorEvidence()
        self.evidence_changed = asyncio.Event()
        self.request_sequence = 0
        self.requests = {}
        self.draft_id = None
        self.observers = set()

    async def observe(self, response, issued):
        path = urlsplit(response.url).path
        if urlsplit(response.url).hostname != "www.pinterest.com" or path not in {"/resource/ApiResource/get/", "/resource/ApiResource/update/"}:
            return
        try:
            await response.finished()
            payload = await response.json()
            timing = response.request.timing
            if timing["startTime"] < 0 or timing["responseEnd"] < 0:
                return
            # Browser timestamps survive delayed Python JSON processing: a GET
            # must start after the save response actually finished downloading.
            self.evidence.record(path, payload, issued, timing["startTime"], timing["startTime"] + timing["responseEnd"])
            self.evidence_changed.set()
        except Exception:
            return  # Missing/changed response evidence must fail the later verification.

    def request(self, request):
        self.request_sequence += 1
        self.requests[request] = self.request_sequence

    def response(self, response):
        issued = self.requests.pop(response.request, None)
        if issued is None:
            return
        task = asyncio.create_task(self.observe(response, issued))
        self.observers.add(task)
        task.add_done_callback(self.observers.discard)

    async def wait_evidence(self, condition):
        async with asyncio.timeout(60):
            while not condition():
                self.evidence_changed.clear()
                await self.evidence_changed.wait()

    async def open_editor(self, expected):
        self.page = await self.context.new_page()
        self.page.on("request", self.request)
        self.page.on("requestfailed", lambda request: self.requests.pop(request, None))
        self.page.on("response", self.response)
        await self.page.goto(expected["profile_url"], wait_until="domcontentloaded")
        await self.verify_identity(expected)
        await self.page.goto(self.contract["editor_url"], wait_until="domcontentloaded")
        await self.verify_identity(expected)
        await self.control("editor")
        await self.control("upload", visible=False)
        await self.wait_evidence(lambda: self.evidence.drafts_loaded)
        await self.page.wait_for_function("ids => ids.every(id => document.querySelector('[data-test-id=\"pinDraft-' + id + '\"]'))", arg=list(self.evidence.drafts))

    async def verify_upload(self, editor):
        video = await self.control("upload_ready", editor)
        await video.evaluate("video => new Promise((resolve,reject) => { if(video.readyState>=1) return resolve(); video.addEventListener('loadedmetadata',resolve,{once:true}); video.addEventListener('error',()=>reject(Error('preview failed')),{once:true}); })")
        state = await video.evaluate("""async video => {
            if (!video.src.startsWith('blob:') || video.readyState < 1 || !video.videoWidth || !video.videoHeight) return null;
            const bytes = await (await fetch(video.src)).arrayBuffer();
            const hash = await crypto.subtle.digest('SHA-256', bytes);
            const view = new DataView(hash);
            let sha256 = '';
            for (let index=0; index<view.byteLength; index++) sha256 += view.getUint8(index).toString(16).padStart(2,'0');
            return {sha256,duration:video.duration};
        }""")
        if not state or state["sha256"] != self.video_sha256 or not 4 <= state["duration"] <= 300:
            raise PublishError("UPLOAD_UNCONFIRMED", "Editor preview differs from the validated private video bytes.", 3)

    async def selected_board(self, details, editor):
        if details["board_url"] != self.contract["board"]["url"]:
            raise PublishError("BOARD_MISMATCH", "Requested board differs from the recorded rehearsal board.")
        if await (await self.control("selected_board_label", editor)).inner_text() != self.contract["board"]["name"]:
            raise PublishError("BOARD_MISMATCH", "Editor selected board name differs from the inspected exact board.")

    async def prepare(self, video, details):
        editor = await self.control("editor")
        self.video_name, self.video_sha256 = video.name, details["video_sha256"]
        previous_ids = set(await self.page.locator('[data-test-id^="pinDraft-"]').evaluate_all('nodes=>nodes.map(node=>node.getAttribute("data-test-id").slice(9))'))
        await (await self.control("upload", editor, visible=False)).set_input_files(str(video))
        await self.verify_upload(editor)
        await self.page.wait_for_function("previous => Array.from(document.querySelectorAll('[data-test-id^=\"pinDraft-\"]')).some(node => !previous.includes(node.getAttribute('data-test-id').slice(9)))", arg=list(previous_ids))
        current_ids = set(await self.page.locator('[data-test-id^="pinDraft-"]').evaluate_all('nodes=>nodes.map(node=>node.getAttribute("data-test-id").slice(9))'))
        new_ids = current_ids - previous_ids
        if len(new_ids) != 1:
            raise PublishError("DRAFT_UNVERIFIED", "Cannot bind exactly one newly uploaded editor draft.", 3)
        self.draft_id = new_ids.pop()
        for name in ("title", "description", "link"):
            value = details[name] or ""
            control = await self.control(name, editor)
            if await self.text_value(name, editor) == value:
                # A no-op fill may not autosave (notably an omitted link). Reuse
                # a fresh matching snapshot rather than wait for a nonexistent edit.
                await self.wait_evidence(lambda: self.evidence.field_saved(self.draft_id, name, value))
                continue
            boundary = self.request_sequence
            await self.pre_submit_click(control, name)
            if name == "description":
                await control.press("Meta+A" if sys.platform == "darwin" else "Control+A")
                await control.press("Backspace")
                for index, line in enumerate(value.split("\n")):
                    if index:
                        await control.press("Enter")
                    await control.press_sequentially(line, delay=5)
            else:
                await control.fill(value)
            await control.press("Tab")
            await self.wait_evidence(lambda: self.evidence.field_saved(self.draft_id, name, value, boundary))
            if await self.text_value(name, editor) != value:
                raise PublishError("TEXT_MISMATCH", f"Editor {name} changed while its draft was saved.")
        await self.pre_submit_click(await self.control("board_menu", editor), "board menu")
        option = await self.control("board_option")
        if await option.inner_text() != self.contract["board"]["name"]:
            raise PublishError("BOARD_MISMATCH", "Board option differs from the inspected rehearsal board.")
        await self.pre_submit_click(option, "rehearsal board option")
        await self.selected_board(details, editor)
        for name in ("ai_modified", "ai_generated_person"):
            await self.set_ai(name, details[name], editor)

    async def verify_board(self, expected, details):
        if details["board_url"] != self.contract["board"]["url"] or details["visibility"] != "public":
            raise PublishError("BOARD_MISMATCH", "Recorded pre-submit rehearsal supports only the exact public board.")
        page = await self.context.new_page()
        try:
            await page.goto(details["board_url"], wait_until="domcontentloaded")
            authenticated_url(page.url)
            if board_url(page.url) != details["board_url"]:
                raise PublishError("BOARD_MISMATCH", "Pinterest did not open the exact rehearsal board.")
            await self.actor("current_actor_link", expected, page)
            if await (await self.control("board_title", page)).inner_text() != self.contract["board"]["name"]:
                raise PublishError("BOARD_MISMATCH", "Exact board title changed.")
            if await (await self.control("board_visibility", page)).inner_text() != "Public board":
                raise PublishError("VISIBILITY_MISMATCH", "Exact board is not public.")
            collaborator = await self.control("board_collaboration", page)
            if await collaborator.get_attribute("aria-label") != f'Collaborators: {expected["display_name"]}. Add collaborators.':
                raise PublishError("COLLABORATIVE_BOARD_UNSUPPORTED", "Board collaborator evidence changed; this rehearsal requires the sole expected owner.")
        finally:
            await page.close()

    async def text_value(self, name, editor):
        control = await self.control(name, editor)
        if name == "description":
            # Draft.js block boundaries encode supplied newlines; no trimming/normalizing.
            return await control.evaluate("element => Array.from(element.querySelectorAll('[data-block=true]')).map(block=>block.textContent).join('\\n')")
        return await super().text_value(name, editor)

    async def verify(self, expected, details):
        await self.verify_identity(expected)
        await self.verify_board(expected, details)
        editor = await self.control("editor")
        await self.verify_upload(editor)
        await self.selected_board(details, editor)
        for name in ("title", "description", "link"):
            if await self.text_value(name, editor) != (details[name] or ""):
                raise PublishError("TEXT_MISMATCH", f"Editor {name} differs from the exact supplied value.")
        for name in ("ai_modified", "ai_generated_person"):
            if await self.ai_state(await self.control(name, editor)) != details[name]:
                raise PublishError("AI_STATE_MISMATCH", "AI disclosure changed before the rehearsal screenshot.")
        publish = await self.control("publish_button", editor)
        if await publish.inner_text() != "Publish":
            raise PublishError("SCHEDULING_UNSUPPORTED", "Final editor control is not the observed immediate Publish action.")
        await publish.click(trial=True)
        self.prepared_request = (expected, details)

    async def submit(self):
        raise PublishError("UI_NOT_VERIFIED", "The recorded live adapter supports only pre-submit rehearsal and cannot click Publish.", 3)

    async def verify_post(self, url, expected, details, page=None, public=False):
        # The live Pin has no literal Published/Public labels. Exact readable video
        # plus the checked original public board and a clean guest view prove access.
        await super().verify_post(url, expected, details, page, public=True)

    async def confirm(self, expected, details):
        raise PublishError("UNCONFIRMED_POST", "No observed response binds this editor's Publish to a concrete Pin; navigation alone cannot confirm publication.", 4)
