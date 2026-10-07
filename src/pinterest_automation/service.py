"""Service entry points; the journal boundary always precedes the sole Publish call."""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import re
import sys
import tempfile

from .browser import ADAPTER_REVISION, BROWSER_BUILD, RUNTIME, editor_adapter, accepted_contract, browser_session, load_contract
from .errors import PublishError
from .firefox import pinterest_cookies
from .media import copy_video, probe_video, read_text
from .state import State, board_url, canonical, destination_url, pin_url, profile_url, result


def normalized(args):
    title, description = read_text(args.title_file, args.description_file, args.metadata_file)
    if args.ai_generated_person == "yes" and args.ai_modified == "no":
        raise PublishError("INVALID_AI_FLAGS", "An AI-generated person also requires --ai-modified yes.")
    board = board_url(args.board_url)
    return {"type": "video_pin", "video": str(Path(args.video).expanduser().absolute()),
            "title": title, "description": description, "board_url": board,
            "visibility": args.visibility, "link": destination_url(args.link),
            "ai_modified": args.ai_modified == "yes", "ai_generated_person": args.ai_generated_person == "yes"}


def prepared(arguments, directory: Path):
    video = directory / ("video" + Path(arguments["video"]).suffix.lower())
    digest = copy_video(arguments["video"], video)
    probe_video(video)
    details = {**arguments, "video_sha256": digest}
    fingerprint = hashlib.sha256(canonical(details).encode()).hexdigest()
    return video, details, fingerprint


async def diagnostic_error(exc, adapter, submitted):
    if isinstance(exc, PublishError):
        error = exc
    else:
        error = PublishError("SUBMISSION_UNCERTAIN" if submitted else "TECHNICAL_FAILURE",
                             "Browser operation failed or timed out; inspect local diagnostics.", 4 if submitted else 3)
    try:
        async with asyncio.timeout(5):
            screenshot = await adapter.screenshot()
        if screenshot:
            error = PublishError(error.code, f"{error.message} Screenshot: {screenshot}", error.exit_code)
    except BaseException:
        pass
    return error


def cleanup_temporary(path: Path):
    try:
        shutil.rmtree(path)
    except Exception:
        print(f"Could not remove private temporary directory {path}; remove it manually.", file=sys.stderr)


async def publish(state: State, args, session_factory=browser_session, adapter_factory=editor_adapter):
    with state.lock(args.account):
        account = state.account(args.account)
        arguments = normalized(args)
        row = state.get(args.account, args.request_id)
        if row:
            if row["arguments"] != canonical(arguments):
                raise PublishError("REQUEST_CONFLICT", "Request ID was already used with different arguments.")
            row = state.recover(row)
            if row["status"] in {"published", "uncertain"}:
                return result(row), 0 if row["status"] == "published" else 4
        request_dir = state.request_dir(args.account, args.request_id)
        temporary = Path(tempfile.mkdtemp(prefix="upload-", dir=request_dir))
        submitted, adapter = False, None
        try:
            video, details, fingerprint = prepared(arguments, temporary)
            if row and row["fingerprint"] != fingerprint:
                raise PublishError("REQUEST_CONFLICT", "Request ID was already used with different text or media bytes.")
            state.start(args.account, args.request_id, arguments, fingerprint, details)
        except BaseException:
            cleanup_temporary(temporary)
            raise
        try:
            contract = accepted_contract(account)
            async with asyncio.timeout(args.timeout_seconds):
                async with session_factory(state.profile(args.account), args.headless) as context:
                    adapter = adapter_factory(context, contract, request_dir / "diagnostics")
                    try:
                        await adapter.open_editor(account)
                        await adapter.prepare(video, details)
                        await adapter.verify(account, details)
                        state.finish(args.account, args.request_id, "submitting")
                        submitted = True
                        await adapter.submit()
                        url = await adapter.confirm(account, details)
                        if not url:
                            raise PublishError("UNCONFIRMED_POST", "A concrete URL for the submitted Pin was not confirmed.", 4)
                        state.finish(args.account, args.request_id, "published", post_url=url)
                    except BaseException as exc:
                        raise await diagnostic_error(exc, adapter, submitted) from exc
            return result(state.get(args.account, args.request_id)), 0
        except BaseException as exc:
            try:
                saved = state.get(args.account, args.request_id)
            except Exception:
                saved = None
            if saved and saved["status"] == "published":
                return result(saved), 0
            submitted = submitted or bool(saved and saved["status"] in {"submitting", "uncertain"})
            error = exc if isinstance(exc, PublishError) else PublishError(
                "SUBMISSION_UNCERTAIN" if submitted else "TECHNICAL_FAILURE",
                "Browser operation failed or was interrupted; read status before retrying.", 4 if submitted else 3)
            status = "uncertain" if submitted else "failed"
            try:
                state.finish(args.account, args.request_id, status, error.json(), adapter.post_url if adapter else None)
                output = result(state.get(args.account, args.request_id))
            except Exception:
                output = {"platform": "pinterest", "account": args.account, "request_id": args.request_id,
                          "status": status, "post_url": None, "error": {"code": "JOURNAL_FAILURE", "message": "Could not persist the result; inspect this request and the journal manually."}}
            return output, 4 if submitted else error.exit_code


async def wait_for_enter():
    if not sys.stdin.isatty():
        raise PublishError("INTERACTIVE_LOGIN_REQUIRED", "Run visible login from an interactive terminal.")
    loop = asyncio.get_running_loop()
    complete = loop.create_future()

    def ready():
        if not complete.done():
            complete.set_result(sys.stdin.readline())

    descriptor = sys.stdin.fileno()
    loop.add_reader(descriptor, ready)
    try:
        if not await complete:
            raise PublishError("INTERACTIVE_LOGIN_REQUIRED", "Login terminal input closed before confirmation.")
    finally:
        loop.remove_reader(descriptor)


async def login(state: State, args, session_factory=browser_session, adapter_factory=editor_adapter):
    url = profile_url(args.profile_url)
    expected = {"profile_url": url, "username": url.split("/")[-2], "display_name": args.display_name}
    if not args.display_name.strip():
        raise PublishError("INVALID_DISPLAY_NAME", "Expected display name must be nonempty.")
    contract = load_contract(args.ui_contract, expected, "login")
    with state.lock(args.account):
        bindings = [account for account in state.accounts() if account["account"] == args.account or account["profile_url"] == url]
        if any(account["account"] != args.account or account["profile_url"] != url for account in bindings):
            raise PublishError("ACCOUNT_BINDING_CONFLICT", "Alias or canonical Pinterest profile is already bound differently.")
        imported = pinterest_cookies(args.firefox_profile) if getattr(args, "firefox_profile", None) else None
        async with asyncio.timeout(args.timeout_seconds):
            async with session_factory(state.profile(args.account), False) as context:
                adapter = adapter_factory(context, contract, state.root / "accounts" / args.account / "diagnostics")
                previous = None
                domain = re.compile(r"^(?:\.)?(?:www\.)?pinterest\.com$")
                try:
                    if imported is not None:
                        previous = [cookie for cookie in await context.cookies() if domain.fullmatch(cookie["domain"])]
                        await context.clear_cookies(domain=domain)
                        await context.add_cookies(imported)
                    adapter.page = await context.new_page()
                    await adapter.page.goto(expected["profile_url"], wait_until="domcontentloaded")
                    if imported is None:
                        print("Sign in, finish 2FA and switch to the explicitly requested Pinterest profile in Camoufox. Press Enter here when ready.", file=sys.stderr)
                        await wait_for_enter()
                    await adapter.verify_identity(expected)
                    state.bind(args.account, expected)
                except BaseException as exc:
                    if previous is not None:
                        await context.clear_cookies(domain=domain)
                        await context.add_cookies(previous)
                    raise await diagnostic_error(exc, adapter, False) from exc
        return {"platform": "pinterest", "account": args.account, "status": "authenticated", "error": None}, 0


async def accept_ui(state: State, args, session_factory=browser_session, adapter_factory=editor_adapter):
    """Visible rehearsal: no journal submission, no call to submit()."""
    with state.lock(args.account):
        account = state.account(args.account)
        contract = load_contract(args.ui_contract, account, "pre-submit")
        directory = state.root / "accounts" / args.account
        temporary = Path(tempfile.mkdtemp(prefix="preflight-", dir=directory))
        try:
            arguments = normalized(args)
            video, details, _ = prepared(arguments, temporary)
            async with asyncio.timeout(args.timeout_seconds):
                async with session_factory(state.profile(args.account), False) as context:
                    adapter = adapter_factory(context, contract, directory / "diagnostics")
                    try:
                        await adapter.open_editor(account)
                        await adapter.prepare(video, details)
                        await adapter.verify(account, details)
                        evidence = await adapter.screenshot("accepted-editor.png")
                        if evidence is None:
                            raise PublishError("ACCEPTANCE_EVIDENCE_MISSING", "Cannot record the visible preflight screenshot.", 3)
                        with evidence.open("rb") as stream:
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                        if contract["schema_version"] in {"1.1.0", "1.2.0"}:
                            await adapter.discard_rehearsal_draft()
                        state.accept_ui(args.account, {"adapter_revision": ADAPTER_REVISION, "runtime": RUNTIME, "browser_build": BROWSER_BUILD,
                            "confirmed_at": datetime.now(timezone.utc).isoformat(), "evidence_sha256": digest,
                            "evidence_path": str(evidence), "contract": contract})
                    except BaseException as exc:
                        raise await diagnostic_error(exc, adapter, False) from exc
            output = {"platform": "pinterest", "account": args.account, "status": "ui_accepted", "evidence_path": str(evidence), "error": None}
            if contract.get("scope") == "pre-submit":
                output.update(scope="pre-submit", publication_enabled=False)
            return output, 0
        finally:
            cleanup_temporary(temporary)


async def reconcile(state: State, args, session_factory=browser_session, adapter_factory=editor_adapter):
    """Explicit manual video attestation plus read-only verification of one Pin."""
    with state.lock(args.account):
        account = state.account(args.account)
        row = state.get(args.account, args.request_id)
        if row is None:
            raise PublishError("REQUEST_NOT_FOUND", "No journal entry for this account and request ID.")
        row = state.recover(row)
        url = pin_url(args.post_url)
        if row["post_url"] and row["post_url"] != url:
            raise PublishError("REQUEST_CONFLICT", "This request already identifies another Pin; its URL cannot be replaced.")
        if row["status"] == "published":
            return result(row), 0
        if row["status"] != "uncertain" or not args.confirm_matching_pin:
            raise PublishError("RECONCILE_UNAVAILABLE", "Manually compare this Pin's account, board, video, exact text, link, visibility and AI disclosures with the journal before reconciliation.")
        adapter = None
        try:
            contract = accepted_contract(account)
            details = json.loads(row["details"])
            async with asyncio.timeout(args.timeout_seconds):
                async with session_factory(state.profile(args.account), args.headless) as context:
                    adapter = adapter_factory(context, contract, state.request_dir(args.account, args.request_id) / "diagnostics")
                    await adapter.identity_page(account)
                    await adapter.verify_post(url, account, details)
                    await adapter.verify_public(url, account, details)
                    state.finish(args.account, args.request_id, "published", post_url=url)
        except BaseException as exc:
            saved = state.get(args.account, args.request_id)
            if saved["status"] == "published":
                return result(saved), 0
            error = await diagnostic_error(exc, adapter, True) if adapter else (exc if isinstance(exc, PublishError) else PublishError("RECONCILE_UNCONFIRMED", "Could not verify the inspected Pin; no Publish was attempted.", 4))
            state.finish(args.account, args.request_id, "uncertain", error.json(), row["post_url"])
            return result(state.get(args.account, args.request_id)), 4
        return result(state.get(args.account, args.request_id)), 0
