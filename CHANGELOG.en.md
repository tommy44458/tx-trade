# Changelog

The product version is managed in `version.json`. Dates record release preparation; public availability is determined by the official download page.

## [Unreleased]

### Added

- Market and position analyses now include a BTC/ETH market reference: the trend, swing structure, and recent change of both on the primary and next higher timeframe from closed candles, plus the analyzed pair's correlation and beta to each. The AI weighs market direction as risk context by the measured linkage and never lets it replace the pair's own levels or invalidation. It is computed locally and adds no model requests.
- Switching pairs in market analysis now shows that pair's latest completed analysis; a just-finished report or one opened from history takes precedence.

## [0.2.0] - 2026-10-02

### Added

- Added a canonical product version, version consistency checks, and a local preparation workflow for Chinese and English release notes.
- Added About and Changelog entries to desktop settings, displaying the actual application version and release notes in the selected language.
- API and local service version information now follows the product version, including consistent beta version notation.
- Added macOS Apple Silicon installer builds in GitHub Actions and a signed draft release workflow; public distribution still requires Apple credentials and installation acceptance.
- Added native update checks, downloads, progress, and installation controls. Release updates wait for model work to finish and back up local data; test packages keep updates disabled.
- Licensed the source code under the GNU AGPL v3 only, and added a Contributor License Agreement (CLA), a trademark policy, and third-party notices. Candlestick charts show the TradingView attribution required by the Lightweight Charts license.
- Added Claude Code as an AI analysis source. It binds to the locally installed, signed-in Claude Code CLI for strategy analysis, macro interpretation, translation, and streamed follow-ups. The app never reads or stores Claude credentials, and the model can use only txTrade's registered indicator tools.
- Settings now detect whether the Codex and Claude Code CLIs are installed, and offer copyable install and sign-in commands when a CLI is missing or signed out. When the app is opened from Finder, common install locations such as Homebrew, npm, Volta, Bun, and nvm are also checked.
- Added a txTrade wordmark app icon.

### Changed

- Renamed the product from the interim AI Trade Helper to txTrade across the application, installers, menus, and the `tommy44458/tx-trade` release repository. On first launch the existing `AI Trade Helper` data folder moves to `txTrade`; while an older build is still running, the original folder stays in use.
- The startup page now presents an animated txTrade wordmark, shown for at least three seconds and faded out before the workspace loads; reduced motion is respected.
- The sidebar no longer shows the internal workspace ID (`local-demo`); local mode shows only "Local workspace".

### Removed

- Removed the direct ChatGPT account (Sign in with ChatGPT) analysis source. Codex and Claude Code are now the connectable AI sources; settings that selected ChatGPT fall back to the default source, and saved ChatGPT authorization tokens are deleted during the database upgrade.
