# Changelog

The product version is managed in `version.json`. Dates record release preparation; public availability is determined by the official download page.

## [Unreleased]

### Changed

- While a newer version is available, a notice stays above the account at the bottom left (available, download progress, ready to restart); selecting it opens the update window, so choosing Later no longer means forgetting it. After Download Update, the notice shows a spinner and progress bar at once and the Dock icon shows the download, so the app never looks stuck. Each time the app opens, it checks within seconds and offers the update instead of after a minute or more.

## [1.0.1] - 2026-10-02

### Changed

- Support and resistance in position analyses are also listed from the highest price down with the analysis-time price marked, matching market analysis.

### Fixed

- When the computer was offline, none was connected yet, or there was no subscription, the remote web page showed neither the signed-in account nor a way to sign out. These screens now show the account at the top right, with a menu to switch computers and sign out.

## [1.0.0] - 2026-10-02

The first public release of txinTrade: crypto futures market and position analysis on your computer, with the Claude Code or Codex you already use. Later versions install through in-app updates, after your data is backed up.

### Added

- Market and position analyses now include a BTC/ETH market reference: the trend, swing structure, and recent change of both on the primary and next higher timeframe from closed candles, plus the analyzed pair's correlation and beta to each. The AI weighs market direction as risk context by the measured linkage and never lets it replace the pair's own levels or invalidation. It is computed locally and adds no model requests.
- Switching pairs in market analysis now shows that pair's latest completed analysis; a just-finished report or one opened from history takes precedence.
- Settings add an optional Cloud account: sign in to the txinTrade cloud with Google, so you can use it remotely from a browser on another device. Sign-in uses the system browser and a local callback, and the app contains no Google client secret; the cloud session is stored encrypted only on this computer, and the sidebar shows the account once signed in. Local features are unchanged without signing in.
- Once signed in to the cloud, a Settings switch allows remote access to this computer from a browser on another device, with a status light showing the connection live. The computer connects out to the cloud in the background and opens no incoming port, The remote web page uses the same screens as the desktop (market analysis, positions, economic events, history, and follow-up questions); every request passes a route allowlist in both the cloud and on the computer, and keys, AI accounts, the cloud account, and updates can only be changed on the computer. Large reports are compressed and sent in parts, and follow-up replies appear live as they are written; with an older app on the computer, the page reads them periodically instead. Remotely started analyses use the locally saved trading preferences, and a retried command never creates a duplicate analysis. Signing out turns remote access off and revokes this computer. After you finish signing in with Google in the browser, the app comes back to the front on its own.
- The analysis chart can toggle each indicator the AI used, with the report's frozen parameters: EMA, rolling VWAP, Bollinger, Keltner, Donchian, Fibonacci retracements, and swing points on the price chart, and RSI, MACD, ATR, OBV, ADX/DMI, and Stochastic in their own panes below. Charted values match the backend calculations.

### Changed

- The sidebar account opens a menu that gathers Settings, the remote access switch, and cloud sign-out; on the remote web page the same menu switches computers and signs out. The top right shows whether you are in local or remote mode.
- Narrow screens use an iOS-style layout: a top navigation bar with the brand and account, and the four main pages in a bottom tab bar; the account and settings no longer disappear at phone widths.
- Support and resistance zones are listed from the highest price down with the current price marked among them, so the resistance above and the support below read at a glance; each zone's type and range share one line, with how it was confirmed beneath.
- Positions are laid out as clear fields: the pair, a long or short tag, and leverage on one line, then entry, quantity, stop loss, take profit, and liquidation price each in its own column. The positions page is a single column, with adding a position manually below the list; an Add position button sits beside the Current positions heading, and with no positions the empty list offers it directly, opening the form and scrolling to it.
- On phones the current price leads the market card and the top row keeps only the local or remote status; secondary text is at least 12px, and the chart section no longer repeats the pair as a heading.
- Exchange position sync fits on one row: each connected exchange is a chip with a status light and its position count; selecting it syncs that exchange, and hovering shows the contract types, sync time, and changes. Sync all syncs every exchange in turn and reports the combined result, and exchanges not yet connected collapse into Connect an exchange, so more exchanges will not lengthen the page.

- The AI no longer sees your directional view (bullish/bearish), so its conclusion cannot simply follow your preference. It assesses long and short separately from the same evidence as reasonable now, conditional, or not suitable now, and the report compares that with your view and warns clearly when it does not fit. Python strategy candidates are no longer labelled or filtered by your directional view; trading style and risk tolerance still shape entry timing and confirmation.

### Fixed

- When the AI occasionally returned a slightly malformed report (for example a missing closing bracket, or text fields wrapped in an extra level), the position recommendation and reasons were shown as raw data. The structure is now repaired and the report displays normally; text cut off mid-string is never guessed at.
- The part of the sidebar account menu that extends past the sidebar was covered by the price chart.
- On the positions page, the support and resistance card touched the direction assessment below it and its first zone touched the chart; with Indicators and calculations open, the last item touched the bottom of the report. The spacing is fixed.
- An imported position without a stop loss or take profit said "Not set", suggesting none existed, although BingX standard contracts never report them through the API. It now says "Not reported by exchange", and Edit becomes "Add stop loss", opening straight at the stop loss field; values added this way are never overwritten by later syncs.
- Restarting the app quickly could fail with "the local backend did not start" because the previous instance's background service still held a data lock. The backend now stops on its own when the app is force-quit, and the background service waits for the old lock instead of failing.

## [0.2.0] - 2026-10-02

### Added

- Added a canonical product version, version consistency checks, and a local preparation workflow for Chinese and English release notes.
- Added About and Changelog entries to desktop settings, displaying the actual application version and release notes in the selected language.
- API and local service version information now follows the product version, including consistent beta version notation.
- Added macOS Apple Silicon installer builds in GitHub Actions and a signed draft release workflow; public distribution still requires Apple credentials and installation acceptance.
- Added native update checks, downloads, progress, and installation controls. Release updates wait for model work to finish and back up local data; test packages keep updates disabled.
- Licensed the source code under the GNU AGPL v3 only, and added a Contributor License Agreement (CLA), a trademark policy, and third-party notices. Candlestick charts show the TradingView attribution required by the Lightweight Charts license.
- Added Claude Code as an AI analysis source. It binds to the locally installed, signed-in Claude Code CLI for strategy analysis, macro interpretation, translation, and streamed follow-ups. The app never reads or stores Claude credentials, and the model can use only txinTrade's registered indicator tools.
- Settings now detect whether the Codex and Claude Code CLIs are installed, and offer copyable install and sign-in commands when a CLI is missing or signed out. When the app is opened from Finder, common install locations such as Homebrew, npm, Volta, Bun, and nvm are also checked.
- Added a txinTrade wordmark app icon.

### Changed

- Renamed the product from the interim AI Trade Helper to txinTrade across the application, installers, menus, and the `tommy44458/txin-trade` release repository. On first launch the development-era `txTrade` or `AI Trade Helper` data folder moves to `txinTrade` (the newer `txTrade` when both exist); while an older build is still running, the original folder stays in use.
- The startup page now presents an animated txinTrade wordmark, shown for at least three seconds and faded out before the workspace loads; reduced motion is respected.
- The sidebar no longer shows the internal workspace ID (`local-demo`); local mode shows only "Local workspace".

### Removed

- Removed the direct ChatGPT account (Sign in with ChatGPT) analysis source. Codex and Claude Code are now the connectable AI sources; settings that selected ChatGPT fall back to the default source, and saved ChatGPT authorization tokens are deleted during the database upgrade.
