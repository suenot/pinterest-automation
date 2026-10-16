"""Recorded Pinterest editor contracts for rehearsal and guarded publication."""

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .browser import PinterestEditor, authenticated_url, browser_session, observed_pin_url
from . import human
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
    publish_scope = value.get("schema_version") == "1.2.0" and value.get("scope") == "publish"
    preflight_scope = value.get("schema_version") == "1.1.0" and value.get("scope") == "pre-submit"
    if set(value) != {"schema_version", "scope", "target", "editor_url", "board", "selectors"} or not (preflight_scope or publish_scope):
        raise PublishError("INVALID_UI_CONTRACT", "Use a recorded pre-submit 1.1.0 or publish 1.2.0 contract.")
    if value["target"] != {name: expected[name] for name in ("profile_url", "username", "display_name")}:
        raise PublishError("ACCOUNT_MISMATCH", "Recorded editor contract target differs from the bound Pinterest profile.")
    selectors = value["selectors"]
    allowed_selectors = SELECTORS | {"selected_draft"} | POST_SELECTORS if publish_scope else SELECTORS
    if not isinstance(selectors, dict) or (set(selectors) != allowed_selectors if publish_scope else set(selectors) not in (SELECTORS, SELECTORS | POST_SELECTORS)) or any(not isinstance(item, str) or not item.strip() for item in selectors.values()):
        raise PublishError("INVALID_UI_CONTRACT", "Record every pre-submit control selector.")
    board = value["board"]
    board_keys = {"url", "name", "id"} if publish_scope else {"url", "name"}
    if not isinstance(board, dict) or set(board) != board_keys or not isinstance(board["name"], str) or not board["name"].strip() or (publish_scope and (not isinstance(board["id"], str) or not re.fullmatch(r"[0-9]+", board["id"]))):
        raise PublishError("INVALID_UI_CONTRACT", "Record the exact board URL and name for this rehearsal.")
    if board_url(board["url"]) != board["url"] or board["url"].split("/")[3] != expected["username"]:
        raise PublishError("BOARD_OWNERSHIP_MISMATCH", "Rehearsal board must belong to the bound canonical username.")
    pinterest_url(value["editor_url"])
    if stage == "publish" and not publish_scope:
        raise PublishError("UI_NOT_VERIFIED", "Recorded live editor acceptance is pre-submit-only: active draft, final board/timing and this Publish's causal result remain unverified.", 3)
    if stage not in {"login", "pre-submit", "publish"}:
        raise PublishError("INVALID_UI_CONTRACT", "Unknown UI contract stage.")
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
            # The rehearsal types exactly as the publisher does: at a pace a
            # hand produces, inserting only what a keyboard cannot type. The
            # old path left 5 ms between keystrokes and pushed Unicode runs in
            # through execCommand, which puts text in with no keystroke at all.
            await control.press("Meta+A" if sys.platform == "darwin" else "Control+A")
            await control.press("Backspace")
            await human.enter_text(self.page, control, value,
                                   read_back=lambda: self.text_value(name, editor))
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

    async def discard_rehearsal_draft(self):
        """Remove only the new draft created by this successful acceptance."""
        if not self.draft_id:
            raise PublishError("DRAFT_UNVERIFIED", "No fresh rehearsal draft is bound for cleanup.", 3)
        row = self.page.locator(f'[data-test-id="pinDraft-{self.draft_id}"]')
        if await row.count() != 1:
            raise PublishError("DRAFT_UNVERIFIED", "Fresh rehearsal draft is no longer unique.", 3)
        await row.get_by_role("button", name="Pin draft actions").click()
        await self.page.locator('[data-test-id="delete-draft-action"]').click()
        dialog = self.page.get_by_role("dialog")
        async with self.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/resource/ApiResource/delete/") as pending:
            await dialog.get_by_role("button", name="Delete", exact=True).click()
        response = await pending.value
        payload = await response.json()
        if (response.status != 200 or payload.get("resource", {}).get("options", {}).get("url") != f"/v3/storypins/drafts/{self.draft_id}/"
                or payload.get("resource_response", {}).get("status") != "success"):
            raise PublishError("DRAFT_CLEANUP_UNVERIFIED", "Pinterest did not confirm deletion of this acceptance's draft.", 3)
        await row.wait_for(state="detached")

    async def submit(self):
        raise PublishError("UI_NOT_VERIFIED", "The recorded live adapter supports only pre-submit rehearsal and cannot click Publish.", 3)

    async def verify_post(self, url, expected, details, page=None, public=False):
        # Story Pin closeup markup differs from ordinary Pin closeups. Its own
        # page proves content; the owner board separately proves membership.
        if public:
            await super().verify_post(url, expected, details, page, public=True)
            guest_page = self.page if page is None else page
            if details["ai_modified"] and await guest_page.get_by_text("AI modified", exact=True).count() != 1:
                raise PublishError("POST_UNCONFIRMED", "Public Pin AI disclosure is missing.", 4)
            return
        page = self.page if page is None else page
        url = observed_pin_url(url)
        await page.goto(url, wait_until="domcontentloaded")
        if observed_pin_url(page.url) != url:
            raise PublishError("POST_UNCONFIRMED", "The requested Pin redirected.", 4)
        post = page.locator('[data-test-id="description-content-container"]')
        await post.wait_for(state="visible")
        await self.actor("post_actor_link", expected, post)
        title = post.locator('[data-test-id="pinTitle"]')
        if await title.count() != 1 or await title.inner_text() != details["title"]:
            raise PublishError("POST_UNCONFIRMED", "Published Pin title differs from this request.", 4)
        expanded = post.locator('[data-test-id="product-description-expand-cta"]')
        if await expanded.count() and await expanded.inner_text() == "See more":
            await expanded.click()
        description = post.locator('[data-test-id="structured-description"]')
        if await description.count() != 1 or await description.inner_text() != " ".join(details["description"].split()):
            raise PublishError("POST_UNCONFIRMED", "Published Pin description differs from this request.", 4)
        if details["link"]:
            link = post.locator('a[href="' + details["link"] + '"]')
            if await link.count() != 1:
                raise PublishError("POST_UNCONFIRMED", "Published Pin destination differs from this request.", 4)
        else:
            external = await post.locator('a[href]').evaluate_all("nodes => nodes.filter(node => { const host = new URL(node.href).hostname; return host !== 'pinterest.com' && !host.endsWith('.pinterest.com'); }).length")
            if external:
                raise PublishError("POST_UNCONFIRMED", "Published Pin unexpectedly has a destination link.", 4)
        video = page.locator('video[data-test-id="duplo-hls-video"]')
        await video.wait_for(state="visible")
        media = await video.evaluate("element => ({duration: element.duration, width: element.videoWidth, height: element.videoHeight})")
        if not 4 <= media["duration"] <= 300 or min(media["width"], media["height"]) <= 0:
            raise PublishError("POST_UNCONFIRMED", "Published Pin video is not readable.", 4)
        if details["ai_modified"] and await page.get_by_text("AI modified", exact=True).count() != 1:
            raise PublishError("POST_UNCONFIRMED", "Published Pin AI disclosure is missing.", 4)
        if not public:
            await self.verify_board(expected, details)
            board = await self.context.new_page()
            try:
                await board.goto(details["board_url"], wait_until="domcontentloaded")
                await board.locator('a[href="/pin/' + url.split("/")[4] + '/"]').first.wait_for(state="attached")
            finally:
                await board.close()

    async def verify_public(self, url, expected, details):
        if details["visibility"] != "public":
            return
        # Persistent Camoufox contexts have no Browser.new_context(). A distinct
        # temporary profile proves the Pin is readable without account cookies.
        temporary = Path(tempfile.mkdtemp(prefix="guest-pin-", dir=self.diagnostics))
        try:
            async with browser_session(temporary / "profile", True) as guest:
                page = await guest.new_page()
                await self.verify_post(url, expected, details, page, public=True)
        finally:
            shutil.rmtree(temporary)

    async def confirm(self, expected, details):
        raise PublishError("UNCONFIRMED_POST", "No observed response binds this editor's Publish to a concrete Pin; navigation alone cannot confirm publication.", 4)


def pin_ids_from_creation(data):
    """Accept only explicit Pin identities in a mutation result, not arbitrary IDs."""
    found = set()
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"pin_id", "published_pin_id", "story_pin_id"} and isinstance(item, str) and re.fullmatch(r"[0-9]+", item):
                    found.add(item)
                elif key in {"pin_url", "url"} and isinstance(item, str):
                    try:
                        found.add(observed_pin_url(item).split("/")[4])
                    except PublishError:
                        pass
                elif key == "id" and isinstance(item, str) and re.fullmatch(r"[0-9]+", item) and value.get("type") in {"pin", "storypin", "storypindata"}:
                    found.add(item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(data)
    return found


class PinterestLivePublisher(PinterestPreflight):
    """One recorded editor publication, with a fresh draft and causal response."""

    def __init__(self, context, contract, diagnostics):
        super().__init__(context, contract, diagnostics)
        self.inner_draft_id = None
        self.publish_ready = asyncio.Event()
        self.publish_urls = set()
        self.publish_tasks = set()
        self.created_pin_ids = set()
        self.active_draft_deleted = False

    async def verify(self, expected, details):
        await super().verify(expected, details)
        if (details["ai_modified"], details["ai_generated_person"]) != (True, False):
            raise PublishError("UI_NOT_VERIFIED", "This live editor evidence covers only AI-modified video without an AI-generated person.", 3)
        selected = await self.control("selected_draft")
        selected_id = await selected.evaluate("element => element.closest('[data-test-id^=pinDraft-]')?.getAttribute('data-test-id')")
        if selected_id != f"pinDraft-{self.draft_id}":
            raise PublishError("DRAFT_UNVERIFIED", "The active sidebar draft differs from the newly uploaded video draft.", 3)
        def saved_draft_matches():
            entry = self.evidence.drafts.get(self.draft_id, {})
            draft, metadata = entry.get("draft", {}), entry.get("metadata", {})
            saved = self.evidence.saves.get(self.draft_id)
            return (saved is not None and self.evidence.drafts_started > saved[1]
                    and entry.get("id") == self.draft_id
                    and metadata.get("pin_title") == details["title"]
                    and draft.get("pin_description") == details["description"]
                    and (draft.get("link") or "") == details["link"]
                    and draft.get("board_id") == self.contract["board"]["id"]
                    and "scheduled_ts" in draft and draft["scheduled_ts"] is None
                    and draft.get("ai_disclosures") == [1])
        try:
            await self.wait_evidence(saved_draft_matches)
        except TimeoutError:
            raise PublishError("DRAFT_UNVERIFIED", "The active draft's final board, timing, text, link and AI state were not saved together after the last edit.", 3) from None
        self.inner_draft_id = self.evidence.drafts[self.draft_id]["draft"]["id"]
        self.before_urls.update(await self.existing_pin_urls())

    def record_shape(self, summary):
        path = self.diagnostics / "publish-response-shapes.jsonl"
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a", encoding="utf-8") as stream:
            stream.write(json.dumps(summary, sort_keys=True) + "\n")

    async def capture_publish(self, response):
        request = response.request
        endpoint = urlsplit(response.url)
        if (request.method != "POST" or endpoint.hostname != "www.pinterest.com"
                or not endpoint.path.startswith("/resource/") or endpoint.path == "/resource/ApiCResource/create/"):
            return
        raw_request = request.post_data or ""
        draft_ids = {self.draft_id, self.inner_draft_id}
        bound = any(re.search(r"(?<![0-9])" + re.escape(item) + r"(?![0-9])", raw_request) for item in draft_ids if item)
        summary = {"path": endpoint.path, "status": response.status, "draft_bound": bound}
        try:
            payload = await response.json()
            resource = payload.get("resource", {}) if isinstance(payload, dict) else {}
            options = resource.get("options", {}) if isinstance(resource, dict) else {}
            result = payload.get("resource_response", {}) if isinstance(payload, dict) else {}
            data = result.get("data") if isinstance(result, dict) else None
            summary["options_path"] = urlsplit(options.get("url", "")).path if isinstance(options, dict) and isinstance(options.get("url"), str) else ""
            summary["result_status"] = result.get("status") if isinstance(result, dict) else None
            summary["data_keys"] = sorted(data) if isinstance(data, dict) else []
            candidates = pin_ids_from_creation(data)
            create_path = summary["options_path"] == "/v3/storypins/" and endpoint.path == "/resource/ApiResource/create/"
            delete_path = summary["options_path"] == f"/v3/storypins/drafts/{self.draft_id}/" and endpoint.path == "/resource/ApiResource/delete/"
            if create_path and isinstance(data, dict) and "story_pin_data_id" in data:
                pin_id = data.get("id")
                if type(pin_id) is int and pin_id > 0:
                    pin_id = str(pin_id)
                if isinstance(pin_id, str) and re.fullmatch(r"[0-9]+", pin_id):
                    candidates.add(pin_id)
            summary["pin_ids"] = sorted(candidates)
            if response.status == 200 and summary["result_status"] == "success":
                if create_path:
                    self.created_pin_ids.update(candidates - draft_ids - {self.contract["board"]["id"]})
                if delete_path and bound:
                    self.active_draft_deleted = True
                if self.active_draft_deleted and len(self.created_pin_ids) == 1:
                    pin_id = next(iter(self.created_pin_ids))
                    url = f"https://www.pinterest.com/pin/{pin_id}/"
                    if url not in self.before_urls:
                        self.publish_urls.add(url)
                if self.publish_urls:
                    self.publish_ready.set()
        except Exception:
            summary["parse_failed"] = True
        try:
            self.record_shape(summary)
        except OSError:
            pass

    async def submit(self):
        if self.submitted:
            raise PublishError("DUPLICATE_SUBMIT", "This session has already reached Publish.", 4)
        self.submitted = True
        if self.prepared_request is None or self.inner_draft_id is None:
            raise PublishError("UNVERIFIED_SUBMIT", "Publish requires the fully verified active draft.", 4)
        await self.verify(*self.prepared_request)
        self.page.on("response", lambda response: self.publish_tasks.add(asyncio.create_task(self.capture_publish(response))))
        editor = await self.control("editor")
        await (await self.control("publish_button", editor)).click(no_wait_after=True)

    async def confirm(self, expected, details):
        await self.publish_ready.wait()
        if len(self.publish_urls) != 1:
            raise PublishError("UNCONFIRMED_POST", "Publish did not yield one causally bound Pin ID.", 4)
        url = next(iter(self.publish_urls))
        self.post_url = url
        await self.verify_post(url, expected, details)
        await self.verify_public(url, expected, details)
        return url
