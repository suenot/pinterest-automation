"""Fail-closed Pinterest adapter activated by a recorded visible preflight.

The blank selector template is not a claim about Pinterest's current live UI.
"""

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit

from . import human
from .errors import PublishError
from .state import board_url, pin_url, pinterest_url, private_dir, profile_url

ADAPTER_REVISION = "pinterest-video-pin-2"
RUNTIME = {"camoufox": "0.5.7", "playwright": "1.60.0"}
BROWSER_BUILD = "official/156.0.1-beta.34"
SELECTORS = {
    "current_actor_link", "profile_visibility", "editor", "upload", "upload_ready", "title", "description", "link",
    "composer_actor_link", "board_menu", "board_option", "selected_board_link", "board_owner_link", "board_visibility",
    "board_collaboration", "ai_modified", "ai_generated_person", "publication_timing", "publish_button", "success",
    "result_link", "post", "post_actor_link", "post_board_link", "post_title", "post_description", "post_link",
    "post_video", "post_status", "post_visibility",
}
LABELS = {"public_profile", "private_profile", "public_board", "private_board", "solo_board", "upload_ready", "immediate", "published"}


def observed_pin_url(value: str):
    """Compare observed Pin identities independently of tracking/fragment variants."""
    url = urlsplit(value)
    return pin_url(urlunsplit((url.scheme, url.netloc, url.path, "", "")))


def authenticated_url(value: str):
    url = pinterest_url(value)
    if url.path.lower().startswith(("/login", "/checkpoint", "/auth")):
        raise PublishError("LOGIN_REQUIRED", "Pinterest requires visible login or 2FA before continuing.")


def validate_contract(value: object, expected: dict, stage="publish") -> dict:
    if isinstance(value, dict) and value.get("schema_version") in {"1.1.0", "1.2.0"}:
        from .preflight import validate_preflight_contract
        return validate_preflight_contract(value, expected, stage)
    keys = {"schema_version", "target", "editor_url", "selectors", "labels"}
    if not isinstance(value, dict) or set(value) != keys or value["schema_version"] != "1.0.0":
        raise PublishError("INVALID_UI_CONTRACT", "Use the complete local UI contract with schema_version 1.0.0.")
    target = {name: expected[name] for name in ("profile_url", "username", "display_name")}
    if value["target"] != target:
        raise PublishError("ACCOUNT_MISMATCH", "UI contract target differs from the explicitly bound Pinterest profile.")
    selectors, labels = value["selectors"], value["labels"]
    if not isinstance(selectors, dict) or set(selectors) != SELECTORS or any(not isinstance(item, str) or not item.strip() for item in selectors.values()):
        raise PublishError("UI_NOT_VERIFIED", "Live Pinterest selectors are not recorded; inspect the blank local contract template before login/accept-ui.", 3)
    if not isinstance(labels, dict) or set(labels) != LABELS or any(not isinstance(item, str) or not item.strip() for item in labels.values()):
        raise PublishError("INVALID_UI_CONTRACT", "Record exact visible state labels for the current Pinterest UI.")
    if labels["public_profile"] == labels["private_profile"] or labels["public_board"] == labels["private_board"]:
        raise PublishError("INVALID_UI_CONTRACT", "Public and private states must be distinguishable.")
    pinterest_url(value["editor_url"])
    return value


def load_contract(path: str, expected: dict, stage="publish") -> dict:
    try:
        document = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise PublishError("INVALID_UI_CONTRACT", "Cannot read the local UI contract.") from exc
    return validate_contract(document, expected, stage)


def accepted_contract(account: dict) -> dict:
    if not account["ui_contract"]:
        raise PublishError("UI_NOT_VERIFIED", "Run a visible accept-ui preflight for this profile before publishing.", 3)
    try:
        record = json.loads(account["ui_contract"])
        if record["adapter_revision"] != ADAPTER_REVISION or record["runtime"] != RUNTIME or record["browser_build"] != BROWSER_BUILD or not record["evidence_sha256"]:
            raise ValueError()
        return validate_contract(record["contract"], account)
    except (ValueError, KeyError, TypeError) as exc:
        raise PublishError("UI_NOT_VERIFIED", "Saved UI acceptance is incompatible; run visible accept-ui again.", 3) from exc


def stable_fingerprint(path: Path, profile: Path | None = None):
    from camoufox.fingerprints import generate_fingerprint

    try:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("navigator"), dict) or not isinstance(value.get("screen"), dict):
                raise ValueError()
            return value
        if profile is not None and profile.exists() and any(profile.iterdir()):
            raise PublishError("FINGERPRINT_INVALID", "Existing browser profile has no saved fingerprint; restore its backup instead of rotating the device identity.", 3)
        fingerprint = generate_fingerprint(os="macos" if sys.platform == "darwin" else "linux")
        temporary = path.with_suffix(".tmp")
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
            json.dump(fingerprint, stream)
        os.replace(temporary, path)
        return fingerprint
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PublishError("FINGERPRINT_INVALID", "Persistent device fingerprint is unreadable; restore its backup instead of silently rotating it.", 3) from exc


@asynccontextmanager
async def browser_session(profile: Path, headless=False):
    from importlib.metadata import version
    from camoufox.async_api import AsyncCamoufox
    from camoufox.multiversion import find_installed_version
    from camoufox.pkgman import launch_path

    if any(version(package) != required for package, required in RUNTIME.items()):
        raise PublishError("RUNTIME_MISMATCH", "Install the pinned Camoufox/Playwright pair with uv sync --locked.", 3)
    override = os.environ.get("CAMOUFOX_EXECUTABLE_PATH", "").strip()
    if override:
        executable = Path(override).expanduser().absolute()
        matching = False
        for parent in list(executable.parents)[:4]:
            metadata = parent / "version.json"
            if metadata.is_file():
                try:
                    build = json.loads(metadata.read_text())
                    matching = build["version"] == "156.0.1" and build["build"] == "beta.34"
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                break
        if not executable.is_file() or not matching:
            raise PublishError("BROWSER_MISMATCH", "Explicit Camoufox executable needs matching 156.0.1-beta.34 version.json metadata.", 3)
    else:
        installed = find_installed_version(BROWSER_BUILD)
        if not installed:
            raise PublishError("BROWSER_MISSING", "Install paired official/156.0.1-beta.34 with python -m camoufox fetch before publishing.", 3)
        executable = Path(launch_path(installed))
    fingerprint = stable_fingerprint(profile.parent / "fingerprint.json", profile)
    mode = "virtual" if headless and sys.platform.startswith("linux") else headless
    try:
        async with AsyncCamoufox(persistent_context=True, user_data_dir=str(profile), executable_path=str(executable), fingerprint=fingerprint,
                                headless=mode, humanize=1.0, locale="en-US", block_images=False) as context:
            context.set_default_timeout(30000)
            yield context
    finally:
        for directory, _, files in os.walk(profile):
            Path(directory).chmod(0o700)
            for name in files:
                path = Path(directory) / name
                if not path.is_symlink():
                    path.chmod(0o600)



def editor_adapter(context, contract, diagnostics):
    if contract["schema_version"] == "1.1.0":
        from .preflight import PinterestPreflight
        return PinterestPreflight(context, contract, diagnostics)
    if contract["schema_version"] == "1.2.0":
        from .preflight import PinterestLivePublisher
        return PinterestLivePublisher(context, contract, diagnostics)
    return PinterestEditor(context, contract, diagnostics)


async def one(locator, description):
    count = await locator.count()
    if count != 1:
        raise PublishError("UI_CHANGED", f"Cannot uniquely locate {description}; found {count} controls.", 3)
    return locator


class PinterestEditor:
    def __init__(self, context, contract: dict, diagnostics: Path):
        self.context, self.contract = context, contract
        self.diagnostics = private_dir(diagnostics)
        self.page = None
        self.submitted = False
        self.post_url = None
        self.video_name = None
        self.video_sha256 = None
        self.prepared_request = None
        self.before_urls = set()
        self.media_attempts = 15
        self.media_interval = 4000

    def selector(self, name: str, scope=None):
        return (scope if scope is not None else self.page).locator(self.contract["selectors"][name])

    async def control(self, name: str, scope=None, visible=True):
        locator = self.selector(name, scope)
        await locator.wait_for(state="visible" if visible else "attached")
        return await one(locator, name)

    async def state_label(self, name, label, scope=None, code="UI_STATE_UNVERIFIED"):
        if await (await self.control(name, scope)).inner_text() != self.contract["labels"][label]:
            raise PublishError(code, f"Cannot verify the requested {name} state.")

    async def guard_control(self, locator, description, click=False):
        """Prove a preparation control cannot reach Publish, and optionally click it.

        The proof is the point: no control is touched before it is shown to be
        neither the final Publish element nor anything that activates it or
        submits a form. Clicking is separate because a text field is clicked
        the way a person clicks one, through the mouse, which is also the only
        click that survives an editor that is busy with a large upload.
        """
        locator = await one(locator, description)
        target = await locator.element_handle()
        final = None
        try:
            final = await (await one(self.selector("publish_button"), "final Publish")).element_handle()
            if target is None or final is None or await target.evaluate(
                """(target, final) => {
                    const overlaps = element => element && (element === final || final.contains(element) || element.contains(final));
                    if (!target.isConnected || !final.isConnected || overlaps(target)) return true;
                    const labels = [target.closest('label'), ...target.querySelectorAll('label')].filter(Boolean);
                    const activated = [target, target.closest('button,input'), ...target.querySelectorAll('button,input'), ...labels.map(label=>label.control)].filter(Boolean);
                    return activated.some(element=>overlaps(element) || (element.form && ['submit','image'].includes(element.type)));
                }""", final
            ):
                raise PublishError("UNSAFE_PRE_SUBMIT_CONTROL", f"{description} can activate Publish or submit an HTML form.", 3)
            if click:
                await target.click()
        finally:
            for handle in (target, final):
                if handle is not None:
                    try:
                        await handle.dispose()
                    except Exception:
                        pass

    async def pre_submit_click(self, locator, description):
        await self.guard_control(locator, description, click=True)

    async def actor(self, name, expected, scope=None):
        link = await self.control(name, scope)
        try:
            actual = profile_url(await self.href(link) or "")
        except PublishError as exc:
            raise PublishError("LOGIN_REQUIRED", "Cannot verify the active Pinterest profile; use visible login.") from exc
        if actual != expected["profile_url"]:
            raise PublishError("ACCOUNT_MISMATCH", "Active Pinterest profile differs from the bound canonical profile.")

    async def href(self, control):
        return await control.evaluate("""element => {
            const href = element.getAttribute('href');
            return href && href.trim() ? new URL(href, element.ownerDocument.baseURI).href : null;
        }""")

    async def verify_identity(self, expected):
        authenticated_url(self.page.url)
        await self.actor("current_actor_link", expected)

    async def identity_page(self, expected):
        self.page = await self.context.new_page()
        await self.page.goto(expected["profile_url"], wait_until="domcontentloaded")
        await self.verify_identity(expected)

    async def open_editor(self, expected):
        await self.identity_page(expected)
        await self.page.goto(self.contract["editor_url"], wait_until="domcontentloaded")
        await self.verify_identity(expected)
        editor = await self.control("editor")
        await self.actor("composer_actor_link", expected, editor)

    async def verify_board(self, expected, details):
        url = board_url(details["board_url"])
        if url.split("/")[3] != expected["username"]:
            raise PublishError("BOARD_OWNERSHIP_MISMATCH", "Only an existing board owned by the bound account is supported.")
        board_page = await self.context.new_page()
        try:
            await board_page.goto(url, wait_until="domcontentloaded")
            authenticated_url(board_page.url)
            if board_url(board_page.url) != url:
                raise PublishError("BOARD_MISMATCH", "Pinterest did not open the exact requested board.")
            await self.actor("current_actor_link", expected, board_page)
            await self.actor("board_owner_link", expected, board_page)
            await self.state_label("board_collaboration", "solo_board", board_page, "COLLABORATIVE_BOARD_UNSUPPORTED")
            await self.state_label("board_visibility", details["visibility"] + "_board", board_page, "VISIBILITY_MISMATCH")
            if details["visibility"] == "public":
                await self.state_label("profile_visibility", "public_profile", board_page, "VISIBILITY_MISMATCH")
        finally:
            await board_page.close()

    async def selected_board(self, details, editor):
        link = await self.control("selected_board_link", editor)
        try:
            selected = board_url(await self.href(link) or "")
        except PublishError as exc:
            raise PublishError("BOARD_UNVERIFIED", "Selected board has no reliable canonical board URL.") from exc
        if selected != details["board_url"]:
            raise PublishError("BOARD_MISMATCH", "Selected editor board differs from the requested canonical board URL.")

    async def set_board(self, details, editor):
        await self.pre_submit_click(await self.control("board_menu", editor), "board menu")
        options = self.selector("board_option")
        matches = []
        for index in range(await options.count()):
            option = options.nth(index)
            try:
                url = board_url(await self.href(option) or "")
            except PublishError:
                continue
            if url == details["board_url"]:
                matches.append(option)
        if len(matches) != 1:
            raise PublishError("BOARD_UNVERIFIED", "Cannot uniquely select the existing board by its canonical URL.")
        await self.pre_submit_click(matches[0], "requested board option")
        await self.selected_board(details, editor)

    async def ai_state(self, control):
        result = await control.evaluate("element => element.matches('input[type=checkbox]') ? element.checked : element.getAttribute('aria-checked')")
        if result is True or result == "true":
            return True
        if result is False or result == "false":
            return False
        raise PublishError("AI_STATE_UNVERIFIED", "AI disclosure control has no readable boolean state.")

    async def set_ai(self, name, enabled, editor):
        control = await self.control(name, editor)
        if await self.ai_state(control) != enabled:
            await self.pre_submit_click(control, name)
        if await self.ai_state(control) != enabled:
            raise PublishError("AI_STATE_MISMATCH", "AI disclosure differs from the explicitly requested state.")

    async def text_value(self, name, editor):
        control = await self.control(name, editor)
        return await control.evaluate("element => ('value' in element ? element.value : element.innerText)")

    async def existing_pin_urls(self):
        urls = set()
        for href in await self.page.locator("a[href]").evaluate_all("links=>links.map(link=>link.href)"):
            try:
                urls.add(observed_pin_url(href))
            except PublishError:
                pass
        return urls

    async def published_text(self, name, post):
        control = await self.control(name, post)
        return await control.evaluate(r"""element => {
            const text = node => node.nodeType === Node.TEXT_NODE ? node.nodeValue :
                ['STYLE', 'SCRIPT'].includes(node.nodeName) || node.getAttribute?.('data-test-id') === 'more-description-container' ? '' :
                node.nodeName === 'BR' ? '\n' : Array.from(node.childNodes).map(text).join('');
            return text(element);
        }""")

    async def verify_upload(self, editor):
        upload = await self.control("upload", editor, visible=False)
        file_state = await upload.evaluate("""async input => {
            if (!input.files || input.files.length !== 1) return null;
            const file = input.files[0];
            const hash = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
            const view = new DataView(hash);
            let digest = '';
            for (let index=0; index<view.byteLength; index++) digest += view.getUint8(index).toString(16).padStart(2,'0');
            return {name: file.name, sha256: digest};
        }""")
        if file_state != {"name": self.video_name, "sha256": self.video_sha256}:
            raise PublishError("UPLOAD_UNCONFIRMED", "Editor file bytes differ from the validated private upload copy.", 3)
        await self.state_label("upload_ready", "upload_ready", editor, "UPLOAD_NOT_READY")

    async def enter_field(self, name, control, editor, value):
        """Clear a field, then enter the value the way a person would.

        fill() hands the field its whole value with no keystroke behind it,
        which is a page's plainest sign that nobody typed. A title or a link
        is short enough to retype, so it is typed; a description is written
        before the upload and pasted, which is what the editor sees from a
        person. An empty value is a clear, not a typed empty string.
        """
        await human.click(self.page, control)
        await control.press("ControlOrMeta+A")
        await control.press("Backspace")
        if value:
            await human.enter_text(
                self.page, control, value,
                read_back=lambda: self.text_value(name, editor))

    async def prepare(self, video, details):
        editor = await self.control("editor")
        self.video_name, self.video_sha256 = video.name, details["video_sha256"]
        await (await self.control("upload", editor, visible=False)).set_input_files(str(video))
        await self.verify_upload(editor)
        for name in ("title", "description", "link"):
            await self.enter_field(name, await self.control(name, editor), editor,
                                   details[name] or "")
        await self.set_board(details, editor)
        for name in ("ai_modified", "ai_generated_person"):
            await self.set_ai(name, details[name], editor)

    async def verify(self, expected, details):
        await self.verify_identity(expected)
        editor = await self.control("editor")
        await self.actor("composer_actor_link", expected, editor)
        await self.verify_board(expected, details)
        await self.selected_board(details, editor)
        await self.verify_upload(editor)
        for name in ("title", "description", "link"):
            if await self.text_value(name, editor) != (details[name] or ""):
                raise PublishError("TEXT_MISMATCH", f"Editor {name} differs from the exact supplied value.")
        for name in ("ai_modified", "ai_generated_person"):
            if await self.ai_state(await self.control(name, editor)) != details[name]:
                raise PublishError("AI_STATE_MISMATCH", "AI disclosure state changed before Publish.")
        await self.state_label("publication_timing", "immediate", editor, "SCHEDULING_UNSUPPORTED")
        success = self.selector("success")
        if await success.count() > 1 or (await success.count() == 1 and await success.is_visible()):
            raise PublishError("STALE_SUCCESS_SURFACE", "A previous success surface is already visible; this editor cannot identify a new Publish result.", 3)
        self.before_urls.update(await self.existing_pin_urls())
        await (await self.control("publish_button", editor)).click(trial=True)
        self.prepared_request = (expected, details)

    async def submit(self):
        if self.submitted:
            raise PublishError("DUPLICATE_SUBMIT", "This session has already reached Publish.", 4)
        self.submitted = True
        if self.prepared_request is None:
            raise PublishError("UNVERIFIED_SUBMIT", "Publish requires the fully verified editor state.", 4)
        await self.verify(*self.prepared_request)
        editor = await self.control("editor")
        await (await self.control("publish_button", editor)).click(no_wait_after=True)

    async def verify_post(self, url, expected, details, page=None, public=False):
        page = self.page if page is None else page
        await page.goto(pin_url(url), wait_until="domcontentloaded")
        if observed_pin_url(page.url) != url:
            raise PublishError("POST_UNCONFIRMED", "Result did not open the specific submitted Pin.", 4)
        post = await self.control("post", page)
        await self.actor("post_actor_link", expected, post)
        board = await self.control("post_board_link", post, visible=not public)
        if board_url(await self.href(board) or "") != details["board_url"]:
            raise PublishError("POST_UNCONFIRMED", "Pin board differs from the requested board.", 4)
        for name in ("title", "description"):
            if await self.published_text("post_" + name, post) != details[name]:
                raise PublishError("POST_UNCONFIRMED", "Published Pin text differs from this request.", 4)
        links = self.selector("post_link", post)
        if details["link"]:
            link = await one(links, "Pin destination link")
            if await link.get_attribute("href") != details["link"]:
                raise PublishError("POST_UNCONFIRMED", "Published Pin destination differs from this request.", 4)
        elif await links.count():
            raise PublishError("POST_UNCONFIRMED", "Published Pin unexpectedly has a destination link.", 4)
        # Pinterest remounts the closeup player while the upload is processed, so
        # the control is re-read on every attempt: a pinned element handle would
        # outlive its node and report an unreadable video forever.
        readable = 'element => element.tagName === "VIDEO" && element.readyState >= 1 && element.videoWidth > 0 && element.videoHeight > 0 && element.duration >= 4 && element.duration <= 300'
        for attempt in range(self.media_attempts):
            if attempt:
                await page.wait_for_timeout(self.media_interval)
            try:
                if await (await self.control("post_video", post)).evaluate(readable):
                    break
            except PublishError:
                raise
            except Exception:
                pass
        else:
            raise PublishError("POST_UNCONFIRMED", "Pin video is not fully accepted and readable.", 4)
        if not public:
            await self.state_label("post_status", "published", post, "POST_UNCONFIRMED")
            await self.state_label("post_visibility", details["visibility"] + "_board", post, "POST_UNCONFIRMED")

    async def verify_public(self, url, expected, details):
        if details["visibility"] != "public":
            return
        browser = self.context.browser
        if browser is None:
            raise PublishError("PUBLIC_ACCESS_UNCONFIRMED", "Cannot open a clean unauthenticated context for the public Pin.", 4)
        public_context = await browser.new_context(locale="en-US")
        try:
            page = await public_context.new_page()
            await self.verify_post(url, expected, details, page, public=True)
        finally:
            await public_context.close()

    async def confirm(self, expected, details):
        success = await self.control("success")
        link = await self.control("result_link", success)
        url = observed_pin_url(await self.href(link) or "")
        if url in self.before_urls:
            raise PublishError("STALE_RESULT_URL", "The success link was already present before Publish; it cannot identify the current submission.", 4)
        # Only the success surface for this sole click supplies causal URL evidence.
        self.post_url = url
        await self.verify_post(url, expected, details)
        await self.verify_public(url, expected, details)
        return url

    async def screenshot(self, name="last-failure.png"):
        if self.page is None or self.page.is_closed():
            return None
        path = self.diagnostics / name
        try:
            await self.page.screenshot(path=str(path))
            path.chmod(0o600)
            return path
        except Exception:
            return None
