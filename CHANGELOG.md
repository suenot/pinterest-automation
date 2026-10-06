# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog, and versions follow Semantic Versioning.

## [Unreleased]

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
