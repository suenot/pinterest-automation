# pinterest-automation

This private project is for a Camoufox-based CLI that publishes one finished local video Pin to an explicitly selected Pinterest account and existing board. Calling services own their queues and schedules.

**Status:** design and repository setup. `pinterest-publish` is not implemented, and this repository cannot publish content. The [Pinterest plan](https://gitlab.com/reforms-lab/root/-/blob/main/docs/publishing/pinterest.md) defines the proposed interface, board visibility, AI disclosure, local request journal, and verification gates. It follows the account, result, locking, and retry rules in the [shared publishing contract](https://gitlab.com/reforms-lab/root/-/blob/main/docs/publishing/contract.md), with Pinterest-specific differences documented in the plan.

The current `metadata-automation` envelope (`schema_version: "1.0.0"`) has no Pinterest block. The initial implementation uses explicit title and description files. A future `--metadata-file` input requires an agreed schema release with `platforms.pinterest`; it must not substitute another platform's text.

Live browser selectors, account and board checks, upload, and final publication have not been implemented or tested. A real acceptance post requires the selected account, board, video, title, description, visibility, destination link or its absence, AI disclosure flags, and approval for that specific publication. See the plan for the complete acceptance criteria.

Version: see `VERSION` while the project has no native package manifest. Changes are recorded in `CHANGELOG.md`. This repository setup is documented under Unreleased and is not a working publisher release.
