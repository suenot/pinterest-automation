# pinterest-automation

`pinterest-publish` publishes one finished local video Pin through persistent Camoufox. It requires an explicitly bound account, an existing owned board, visibility, both AI disclosure flags, and exact title/description. Calling services own queues and schedules.

The local CLI and controlled browser fixtures are implemented. **The current live Pinterest selectors and final publication have not been accepted by this package.** The supplied UI contract template is deliberately blank. Publication returns `UI_NOT_VERIFIED` until a complete contract passes a visible `accept-ui` rehearsal. That rehearsal uploads a private copy and checks the final editor without clicking Publish; Pinterest may save a draft. Review the draft in Pinterest before later publication.

A separate manual Firefox run on 2026-10-06 confirmed `@caribbeanawesome`, the public owner board `caribbean-awesome-videos`, acceptance of a 12,977,531 byte 1080×1920 H.264/AAC video lasting 38.1 seconds, both AI controls, and a publicly readable video Pin. The editor showed `Publishing...` and then reset without a causal result URL. This establishes manual product behavior; authenticated Camoufox selectors, exact selected-board URL evidence, processing/timing/published-state evidence and automatic confirmation remain unaccepted. The CLI must return `uncertain` after submission when the causal URL is unavailable.

The native version source is `pyproject.toml`; `CHANGELOG.md` records releases. This project follows the [Pinterest publishing plan](https://gitlab.com/reforms-lab/root/-/blob/main/docs/publishing/pinterest.md) and [shared publisher contract](https://gitlab.com/reforms-lab/root/-/blob/main/docs/publishing/contract.md).

## Install

Python 3.12, `uv`, Git/SSH access to the pinned private `metadata-automation` repository, and `ffmpeg`/`ffprobe` are required. On macOS install ffmpeg with Homebrew; on Linux install ffmpeg and the platform packages required by Camoufox. Then:

```sh
uv sync --locked --python 3.12
uv run python -m camoufox fetch
export PINTEREST_PUBLISH_STATE_DIR="$PWD/.local"
uv run pinterest-publish --version
```

The pinned pair is `camoufox==0.5.7`, `playwright==1.60.0`, and browser `official/156.0.1-beta.34`. The CLI verifies the package versions and selects only the paired browser. An explicit `CAMOUFOX_EXECUTABLE_PATH` must have matching release `version.json` in a parent directory; an isolated macOS installation also needs `application.ini` next to the executable, linked from `../Resources/application.ini`, for the upstream wrapper. It never replaces the global active browser while publishing. See the upstream [persistent context setup](https://camoufox.com/python/usage/) and [Linux virtual display setup](https://camoufox.com/python/virtual-display/).

Each alias has a separate persistent profile and a saved device fingerprint. Missing or unreadable fingerprints are never silently rotated. Visible mode is the default. `--headless` uses Camoufox's virtual display on Linux and ordinary headless mode on macOS. The default command deadline is 1800 seconds; `--timeout-seconds` overrides it. macOS controlled tests use the paired explicit browser; target Linux/Xvfb and default browser-cache selection still need their own acceptance.

## Bind the account and accept the editor

Copy `examples/ui-contract.template.json` into `.local/ui-contract.json`. In a visible Pinterest session, record selectors and exact state labels for the current UI. The target is `Caribbean Awesome`, `@caribbeanawesome`; its canonical profile URL is the identity. Display names alone cannot establish identity. Every selector must resolve uniquely in its documented scope. Selectors for logged-in identity must identify the active account controls, not arbitrary public profile links.

```sh
uv run pinterest-publish login --account caribbeanawesome \
  --profile-url https://www.pinterest.com/caribbeanawesome/ \
  --display-name 'Caribbean Awesome' --ui-contract .local/ui-contract.json
uv run pinterest-publish accounts list
```

Finish login and 2FA in the visible window, then press Enter in the terminal. A bound alias cannot move to another profile, and a profile cannot acquire a second alias. The profile lock covers login, acceptance, publication and reconciliation; a busy profile fails immediately.

The contract contains `schema_version: "1.0.0"`, the exact `target`, `editor_url`, CSS `selectors`, and `labels`. These version numbers describe the local editor evidence separately from editorial metadata. Canonical platform URLs are restricted to `https://pinterest.com` and `https://www.pinterest.com`, without credentials, queries or fragments. Short links, regional redirect URLs, unrelated hosts and ambiguous board paths are rejected. Host/path policy remains deliberately narrow until live acceptance proves another form necessary.

The selectors identify: the signed-in account link; profile visibility; editor, upload input/readiness, title, description, link and composer account; board menu/options/selected canonical link; board owner, visibility and collaboration state on the exact board URL; both AI controls; immediate publication timing; final Publish; this click's success/result link; and the exact Pin article, author, board, title, description, destination, video, published state and visibility. State labels must come from readable current UI evidence. AI controls must be native checkboxes or expose `aria-checked="true|false"`. No guessed selectors or fallback clicks can reach Publish. Preparation controls are checked for the final Publish element, its click area, associated HTML labels and form submission before they are clicked.

Select an existing board you own. Collaborative boards are outside v1. `--visibility` asserts the board's current state; it never changes board/profile settings. Public publication also requires a public profile. A private board is visible to its owner and anyone invited, according to [Pinterest's private-board help](https://help.pinterest.com/en/article/secret-boards). [Profile visibility also controls access to boards and Pins](https://help.pinterest.com/en/article/make-your-profile-private).

```sh
uv run pinterest-publish accept-ui --account caribbeanawesome \
  --ui-contract .local/ui-contract.json --video /absolute/path/video.mp4 \
  --board-url https://www.pinterest.com/caribbeanawesome/caribbean-awesome-videos/ \
  --visibility public --title-file title.txt --description-file description.txt \
  --ai-modified yes --ai-generated-person no
```

This records a private screenshot, its SHA-256, adapter revision, runtime and browser build. It never calls `submit`. Publication rechecks account, exact board ownership and visibility, accepted upload bytes/readiness, text/link, AI state, and immediate timing; acceptance is invalidated when the runtime/adapter evidence is incompatible. Unknown or missing UI evidence stops before the submission boundary. Record selectors for post confirmation from a previously inspected real video Pin before activating publication. A preflight alone cannot demonstrate the final success surface or prove a live publication.

## Publish

[Official organic Pin specs](https://help.pinterest.com/en/article/review-pin-specs), checked 2026-10-06, allow MP4/M4V with H.264/H.265 and 4–300 seconds, a title up to 100 characters and a description up to 800. Full-screen video is recommended at 9:16 or 1080×1920; other ratios are supported. The CLI validates actual copied container/codec/duration/dimensions with ffprobe. It does not transcode or trim. A live web-editor rejection observed 2026-10-06 stated that videos cannot exceed **200 MB**; this CLI conservatively limits both source and copied bytes to **200,000,000**. The help page's 20 MB limit is for images.

Manual text files are UTF-8 and read exactly, preserving spaces and line endings. Both are required; empty or oversized text fails before the browser opens. An optional `--link` is a separate HTTPS destination and is never a media source. Without it the editor's destination field must be empty. Both AI flags require explicit `yes|no`; an AI-generated person requires `--ai-modified yes`. [Pinterest's creation guide](https://help.pinterest.com/en/article/create-a-pin-from-an-image-or-video) describes the two AI disclosures and Publish later; this CLI requires immediate publication.

```sh
uv run pinterest-publish publish --account caribbeanawesome --request-id pin-20261006-001 \
  --video /absolute/path/video.mp4 \
  --board-url https://www.pinterest.com/caribbeanawesome/caribbean-awesome-videos/ \
  --visibility public --title-file title.txt --description-file description.txt \
  --ai-modified yes --ai-generated-person no
uv run pinterest-publish status --account caribbeanawesome --request-id pin-20261006-001
```

Photo Pins, batches, board creation/settings changes, cover editing, product/topic tags, scheduling, URL media, ads, generation, a server and local UI are outside v1. Unsupported required editor choices stop the request.

## Editorial metadata

Use `--metadata-file` instead of **both** manual files. The complete envelope must have `schema_version: "2.0.0"` and all ten platform blocks. `metadata-automation` 0.2.0 is pinned by commit `994ecce4c904c9c1a7f5de9086ef2a9785c25efa`; its own packaged schema and `validate` check the entire input before selecting `platforms.pinterest.title` and `.description`. Schema v1, flat Pinterest objects, unknown versions, extra fields, missing other platforms and mixed sources fail before the browser. No remote `$ref` is loaded from input. Description already includes its hashtags; the publisher does not append the `hashtags` array. Account, video, request ID, board, visibility, link and AI flags remain explicit CLI parameters.

```sh
uv run pinterest-publish publish --account caribbeanawesome --request-id pin-20261006-002 \
  --video /absolute/path/video.mp4 --metadata-file metadata-v2.json \
  --board-url https://www.pinterest.com/caribbeanawesome/caribbean-awesome-videos/ \
  --visibility public --ai-modified yes --ai-generated-person no
```

## Results and recovery

Every command, including help, version and parser errors, writes exactly one JSON object on one stdout line. Diagnostics use stderr. `publish`, `status` and `reconcile` results contain `platform: "pinterest"`, `account`, `request_id`, `status`, `post_url`, and `error: {code,message}|null`. `login` reports `authenticated`; `accounts list` reports `ok` plus alias/profile identities without session data.

| Exit | Publication result |
| --- | --- |
| 0 | Confirmed or cached `published`. |
| 2 | Invalid input/media, ID conflict, wrong/unknown account, login required, board/visibility/AI/text mismatch. |
| 3 | Technical failure, busy profile, absent browser/runtime or unverified/changed UI before submission. |
| 4 | New or saved `uncertain` after the submission boundary; no automatic retry. |

`status` is read-only: 0 for an existing row in any state, 2 for a missing/invalid account/request pair. It reports a crashed `submitting` row as `uncertain` without mutating the journal. Successful login/list/acceptance return 0; their input failures return 2 and technical failures 3.

SQLite has unique `(account, request_id)` keys and `preparing`, `submitting`, `published`, `uncertain`, `failed` states. The fingerprint includes the resolved source path, private uploaded video SHA-256, exact text, canonical board, visibility, destination and both AI flags. A failed request can retry only with the original arguments and bytes. Cached published/uncertain outcomes compare original normalized arguments and return without reading video bytes, opening a browser, or submitting again.

A FULL synchronous SQLite transaction commits `submitting` **before the sole Publish click**. Any interruption, timeout, processing state or ambiguous confirmation afterward becomes `uncertain`. There is no second click or automatic retry. Confirmation must come from the success surface for this request, and its numeric Pin identity must be absent from all pre-click page links, including hidden links and query/fragment variants. Observed result links are canonicalized for that comparison and output. The specific Pin must match its account, board, exact text/link, actual video, published state and visibility. A public Pin is additionally inspected in a clean unauthenticated browser context. Private Pins are checked only within the signed-in account. Unrelated same-caption Pins and profile differences cannot identify this click. `post_url` is saved only when causally obtained from that success surface or explicitly manually reconciled.

For `uncertain`, inspect the original Pin in Pinterest and compare its account, board, video, exact text/link, visibility and AI disclosures with the journal. Do not use a new request ID to bypass uncertainty. Then explicitly attest the match; reconciliation opens only the exact Pin and checks it without uploading or opening the editor:

```sh
uv run pinterest-publish reconcile --account caribbeanawesome --request-id pin-20261006-001 \
  --post-url https://www.pinterest.com/pin/123456789/ --confirm-matching-pin
```

The supplied URL cannot replace a different saved URL. Reconciliation failure leaves the request uncertain. Browser checks cannot establish the original video bytes from Pinterest's transcoded playback; the explicit manual video comparison is required.

For another process, invoke the executable with an argument list and parse its single stdout line; use the process return code alongside the JSON:

```python
import json
import subprocess

completed = subprocess.run([
    "/absolute/path/pinterest-automation/.venv/bin/pinterest-publish",
    "status", "--state-dir", "/absolute/path/pinterest-automation/.local",
    "--account", "caribbeanawesome", "--request-id", "pin-20261006-001",
], capture_output=True, text=True)
result = json.loads(completed.stdout)
```

## Local data and verification

`.local/` is excluded from Git. The state root, profiles, request directories and diagnostic directories use 0700; media snapshots, fingerprints, screenshots and journal use 0600. Cookie/storage state, passwords, full DOM and publication text are not written to ordinary logs. The private SQLite journal necessarily stores exact request text for idempotency. Failure screenshots may contain private UI. Keep profiles/fingerprints backed up together and never delete the journal to bypass an uncertain request. Remove diagnostic PNGs and obsolete private upload directories manually when no command is running and request recovery is complete; retain the journal and account bindings. Acceptance's temporary upload is removed after its rehearsal.

```sh
CAMOUFOX_EXECUTABLE_PATH=/absolute/path/to/paired/camoufox \
  uv run --group dev pytest -q
```

Unit checks cover one-line JSON/codes, metadata v2 validation, video/text boundaries, snapshots/source mutation, locks, binding collisions, ID conflicts, UI acceptance, durable transitions, cached outcomes and manual reconciliation. Controlled Camoufox pages intercept every browser request and cover preflight without Publish, one-click confirmation, account/board/visibility/AI/text/upload/timing mismatches, unsafe preparation selectors, ambiguous/processing results and public/private confirmation paths. Without the explicit paired executable, controlled browser tests are skipped. Live editor selectors, exact board inspection state, success confirmation, actual upload acceptance and any real publication remain acceptance gates; controlled pages cannot establish them.
