# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog, and versions follow Semantic Versioning.

## [Unreleased]

### Fixed

- Enter a Pin field again when the editor drops it. Pinterest re-renders the
  Create Pin editor while it finishes with an uploaded video, and field text
  that is not saved by then is gone: on 2026-10-16 two rehearsals and two
  publications of a 74 MB video lost a typed title or a pasted description
  that way, then waited out a deadline for a save that could never arrive and
  left their drafts behind. A field whose save does not arrive, and whose text
  is no longer in the control, is entered again, and a Playwright timeout on a
  control that the same re-render replaced under an action in progress is that
  field again rather than an unexplained `TECHNICAL_FAILURE`. Text that stands
  in the control and is still not saved now fails as `TEXT_NOT_SAVED`, and a
  control that never steadies as `TEXT_NOT_ENTERED`.
- Wait for the editor to answer before entering anything into it. Pinterest
  keeps the Create Pin main thread for minutes at a time while it works
  through a large upload, and every driver action needs that thread: the ones
  with a deadline wait it out on an element that is sitting perfectly still,
  and the mouse and keyboard, which have no deadline, never return at all. A
  publication of a 74 MB video on 2026-10-16 spent 60 seconds in
  `scroll_into_view_if_needed` on a motionless title field, three times over,
  and a rehearsal spent three 150-second attempts on the link. Each field now
  waits for a trivial page script to answer promptly before it is entered, and
  an editor that never answers fails as `EDITOR_BUSY` instead of as a stalled
  request.
- Click a control without the driver's stability contract. Its scroll refuses
  to act until an element has held the same box across two animation frames,
  which a page re-rendering under an upload does not always give it; a hand
  does not wait for a page to hold still either. The control is scrolled into
  view by the page's own `scrollIntoView`, and its box is read a second time
  after the approach, so a control that moved under the hand is still pressed
  where it now is.
- Click a Pin text field the way a person clicks one, and prove it safe
  separately. The rehearsal clicked title, description and link through the
  driver, whose hit testing the editor refuses while it is busy with a large
  upload: three attempts at a title that nothing was covering each waited out
  60 seconds, and the request failed with no text entered. The control is
  still proved unable to activate Publish or submit a form before anything
  touches it, through the same guard, which no longer clicks; the click is the
  mouse click the text entry already makes. Each attempt now also has its own
  deadline, because the page script a guard needs has none of its own and a
  busy editor can leave it waiting indefinitely.
- Wait for the uploaded draft on the upload's own deadline rather than the 60
  seconds a control is given. Pinterest creates the sidebar draft only once it
  has taken the whole file, and one 74 MB rehearsal spent 56 of those 60
  seconds there; the command's own timeout still bounds the wait.
- Run the controlled browser checks over the real input path again by
  compressing its pauses in those checks only. Entering a title, a caption and
  a link at a person's pace takes minutes, which no 10-second controlled
  request can hold; the keystrokes, the mouse and the events the page receives
  are unchanged, and the pace itself stays `test_human`'s subject.
- Accept a video whose container writes `major_brand` twice. ffprobe joins
  repeated tags with a semicolon, so a HyperFrames render reporting
  `isom;isom` was refused with `INVALID_MEDIA` although it is an ordinary MP4.
- Type the Pin title, description and link instead of `fill`, which hands a field its whole value with no keystroke behind it. An empty value is still a clear rather than a typed empty string.
- Type the rehearsal's description the same way the publisher types it. The preflight used `press_sequentially(delay=5)` for ASCII and `execCommand("insertText")` for Unicode runs; a rehearsal that enters text differently from the publisher is not a rehearsal of the publisher.

### Added

- `human` module for input that leaves the trace a person leaves: an eased approach to a control, a pause before pressing, typing with a varying interval, insertion for what a keyboard cannot produce, and a real clipboard paste. The paste is the default for text a person would not retype from memory (long, multi-line or outside ASCII), because nobody composes a caption in an upload form; short single-line ASCII is typed. `allow_clipboard=False` turns the paste off. The clipboard is global, so it is re-read immediately before the paste key and the paste is abandoned if it is no longer ours. Kept in step with the copies in the other publishers, which are standalone repositories sharing no package. The measurements behind it are in `docs-site/@docs/guides/what-a-page-sees-when-we-type.md`.
- Test that fails the build if any module in the package dispatches a browser event or puts text in without keystrokes through `execCommand`, `fill`, `insert_text` or `press_sequentially`.

## [0.3.1] - 2026-10-08

### Fixed

- Clean up the exact fresh draft bound during a failed editor acceptance before Publish when the draft's own UI delete is available. Preserve the original failure and report its draft ID if cleanup cannot be confirmed.

## [0.3.0] - 2026-10-08

### Added

- Add a guarded live editor contract that binds the selected upload draft to a fresh saved board, immediate timing, exact text/link and AI disclosure before the journaled single Publish click.
- Observe only safe response shapes and numeric IDs from the browser's own publication request; require one Story Pin create ID followed by a successful deletion of the exact active draft before claiming a published result.

### Fixed

- Insert non-ASCII Pinterest description characters with the browser's edit command so Draft.js saves the exact approved Unicode text.
- Verify live Story Pin details in both the signed-in and different signed-out layouts, including exact normalized description, destination, AI label, video and owned public-board membership.
- Delete only the fresh draft created by a successful editor acceptance after its screenshot has been recorded, preventing repeated rehearsals from accumulating drafts.

## [0.2.0] - 2026-10-07

### Added

- Import ordinary Pinterest cookies from an explicitly selected Firefox profile through `login --firefox-profile`, verify the exact signed-in canonical account in persistent Camoufox, and restore prior Pinterest cookies if verification fails.
- Add a recorded Caribbean Awesome pre-submit contract with a separate no-Publish Camoufox rehearsal for the current editor's blob preview, exact metadata, public board and AI disclosures. Schema 1.1.0 cannot enable publication while active-draft, final board/timing and causal Publish-result binding remain unverified.
- Enter Draft.js description through native keyboard events and serialize title/description/link saves using request-issued ordering; late responses to earlier draft GETs cannot establish saved text freshness.

### Fixed

- Preserve host-only cookie domain/path semantics, convert Firefox millisecond expiry to Playwright seconds, and exclude expired, container and partitioned cookies without changing Firefox files.
- Verify public Pin readability in a clean Camoufox context without requiring authenticated Published/Public labels; keep author, board, exact text/link and video checks scoped to the specific Pin.
- Exclude description expansion controls and style/script nodes from exact published text comparisons.

## [0.1.0] - 2026-10-06

### Added

- Implement the reusable `pinterest-publish` CLI with visible persistent Camoufox login, stable device fingerprint, isolated account profiles, and process locks.
- Validate private MP4/M4V upload snapshots, actual H.264/H.265 container and 4–300 second duration, conservative 200,000,000 byte web limit, exact title/description and explicit board/visibility/link/AI parameters.
- Validate complete metadata-automation v2 envelopes using its pinned 0.2.0 release and use Pinterest title/description without appending hashtags.
- Add a fail-closed UI contract and visible editor acceptance without Publish, SQLite request fingerprints and durable one-click submission, cached outcomes, status, and explicit manual reconciliation.
- Confirm only the submitted Pin's causal success URL, account, board, exact text/link, processed video and visibility, including a clean guest check for public Pins.
- Pin Camoufox/Playwright, browser runtime requirements and uv lock; add unit and controlled browser fixtures. Live Pinterest and target Linux acceptance remain pending.
