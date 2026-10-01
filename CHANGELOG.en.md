# Changelog

The product version is managed in `version.json`. Dates record release preparation; public availability is determined by the official download page.

## [Unreleased]

### Added

- Added a canonical product version, version consistency checks, and a local preparation workflow for Chinese and English release notes.
- Added About and Changelog entries to desktop settings, displaying the actual application version and release notes in the selected language.
- API and local service version information now follows the product version, including consistent beta version notation.
- Added macOS Apple Silicon installer builds in GitHub Actions and a signed draft release workflow; public distribution still requires Apple credentials and installation acceptance.
- Added native update checks, downloads, progress, and installation controls. Release updates wait for model work to finish and back up local data; test packages keep updates disabled.
- Added Claude Code as an AI analysis source. It binds to the locally installed, signed-in Claude Code CLI for strategy analysis, macro interpretation, translation, and streamed follow-ups. The app never reads or stores Claude credentials, and the model can use only txTrade's registered indicator tools.

### Changed

- Renamed the product from the interim AI Trade Helper to txTrade across the application, installers, menus, and the `tommy44458/tx-trade` release repository. On first launch the existing `AI Trade Helper` data folder moves to `txTrade`; while an older build is still running, the original folder stays in use.
- The startup page now presents an animated txTrade wordmark, shown for at least three seconds and faded out before the workspace loads; reduced motion is respected.

### Removed

- Removed the direct ChatGPT account (Sign in with ChatGPT) analysis source. Codex and Claude Code are now the connectable AI sources; settings that selected ChatGPT fall back to the default source, and saved ChatGPT authorization tokens are deleted during the database upgrade.

### Unreleased baseline

- The existing local desktop packaging baseline is 0.2.0; no published release history has been established.
