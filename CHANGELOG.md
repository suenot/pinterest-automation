# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog, and versions follow Semantic Versioning.

## [Unreleased]

## [0.1.0] - 2026-10-06

### Added

- Implement the reusable `pinterest-publish` CLI with visible persistent Camoufox login, stable device fingerprint, isolated account profiles, and process locks.
- Validate private MP4/M4V upload snapshots, actual H.264/H.265 container and 4–300 second duration, conservative 200,000,000 byte web limit, exact title/description and explicit board/visibility/link/AI parameters.
- Validate complete metadata-automation v2 envelopes using its pinned 0.2.0 release and use Pinterest title/description without appending hashtags.
- Add a fail-closed UI contract and visible editor acceptance without Publish, SQLite request fingerprints and durable one-click submission, cached outcomes, status, and explicit manual reconciliation.
- Confirm only the submitted Pin's causal success URL, account, board, exact text/link, processed video and visibility, including a clean guest check for public Pins.
- Pin Camoufox/Playwright, browser runtime requirements and uv lock; add unit and controlled browser fixtures. Live Pinterest and target Linux acceptance remain pending.
