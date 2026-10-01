# txTrade

A local desktop workspace for AI-assisted crypto futures analysis, built with Electron, React + Vite, and FastAPI. It combines current price action, support/resistance, technical indicators, and U.S. macroeconomic data to explain market and position decisions.

txTrade provides analysis and read-only position imports. It does not place orders, close exchange positions, change leverage, transfer funds, or withdraw assets. AI output and support/resistance backtests do not establish profitable trading performance.

## Features

- Search all currently tradable Binance **USDT perpetual** markets and pin favorites. USDC, coin-margined, and delivery contracts are excluded.
- Analyze markets or selected positions with bullish/bearish preferences, low/medium/high risk tolerance, left/right entry style, and leverage selected or entered manually from 1–125×.
- Read clear AI recommendations: enter or wait; hold or close an existing position; supporting prices, reasons, and conditions that would change the assessment. Calculations inform the Agent rather than decide its strategy.
- Explore interactive candlestick charts with current quotes and **v3** support/resistance zones. Forming candles are distinguished from closed candles; historical reports retain their original evidence and chart snapshots.
- Review official CPI, employment, PCE, GDP, and FOMC evidence with a shared AI macro interpretation. New or revised indicator evidence invalidates the cached interpretation; unchanged evidence reuses it.
- Ask follow-up questions in a streaming AI sidebar tied to that analysis or macro interpretation. Conversations retain the original report and language. Each market or position follow-up fetches the same pair’s latest public price and recent primary-timeframe candles, with the quote and retrieval time shown alongside that reply. Original zones, indicators, positions, and macro evidence stay unchanged; unavailable live data is marked explicitly.
- Selecting a pair in My positions restores its latest completed position analysis, saved timeframe, chart, and conversation. Viewing a saved result does not run a new analysis; changed positions remain marked as stale.
- Save preferences, favorites, positions, history, conversations, and app-managed credentials locally. Choose system/light/dark appearance and Traditional Chinese/English; both prompt languages share versioned policies.

### Timeframes and indicators

Every analysis includes the selected timeframe and its next three higher frames:

| Primary | Higher context |
| --- | --- |
| `1h` | `4h`, `12h`, `1d` |
| `4h` | `12h`, `1d`, `3d` |
| `12h` | `1d`, `3d`, `1w` |
| `1d` | `3d`, `1w`, `1M` |

`1M` means calendar-month candles, not one-minute candles. Missing history or insufficient indicator warmup is reported as unavailable.

MA/EMA, RSI, MACD, ATR, volume, rolling VWAP, and confirmed swing points are precomputed. In **Settings → Indicators calculated before AI analysis**, select Bollinger, Fibonacci, ADX/DMI, OBV, Donchian, Keltner, or Stochastic for the first model input. These seven default to unchecked; unselected tools remain available on demand. Saving a selection applies to future analyses without starting a model request.

v3 zones identify possible price friction, not guaranteed reversals. A quote inside a zone is displayed as such; an intrabar crossing alone does not erase the original zone. v4 remains an experimental shadow calculation and is not the strategy reference.

## Run the desktop app

From a checkout, install **Node.js 24+**, **pnpm**, **Python 3.12+**, and **uv**, then run from the repository root:

```bash
pnpm desktop
```

The launcher installs app dependencies, builds the interface, and starts Electron with its private local API and workers. SQLite is created automatically; PostgreSQL and a database password are not required. Desktop setup does not require a `.env` file.

In **Settings**, choose a model provider and connect your account:

- **Codex:** uses an installed Codex CLI through its app-server and authorized account. Connect through the app if an existing usable CLI authorization is unavailable.
- **Claude Code:** uses an installed Claude Code CLI and its existing sign-in. Sign in with `claude auth login` in a terminal, then choose **Connect Claude Code**. The app only checks `claude auth status` and never reads or stores Claude credentials; **Disconnect** unbinds the app without signing Claude Code out. Built-in Claude Code tools, user settings, hooks, plugins, and MCP servers are disabled for analysis runs.
- Existing **OpenAI API compatibility mode** is retained, but an API key is not required for Codex/Claude Code desktop analysis.

Use each provider's own sign-in flow; do not paste browser cookies or extracted session tokens. The app does not copy shared Codex token files or Claude Code credentials. Model requests still require internet access and send the analysis context to the selected provider. This implementation is not a promise of unrestricted subscription access or approval for a hosted/commercial integration.

Keep the app open while work is running: closing it stops its local API and workers.

### Optional integrations

Enter optional keys in **Settings**. Desktop integration keys are saved locally, not imported from `.env` or returned to the interface.

| Integration | Purpose |
| --- | --- |
| Binance API Key + Secret | Import USDT perpetual positions from a regular USDⓈ-M futures account. Portfolio Margin and other contract types are unsupported. |
| BingX API Key + Secret | Import supported perpetual and Standard Futures positions mapped to Binance USDT perpetual markets. |
| Typesafe / Jev | Classify eligible official news in the background. Without a key, classification is skipped; official data collection continues. |
| JBlanked | Optional economic-calendar source checks, subject to provider permissions and credits. Consensus forecasts are not yet Agent evidence. |

For Binance, enable **Enable Reading only**. For BingX, use a **read-only key**. Do not enable trading, transfer, or withdrawal permissions for either integration.

For Binance, save the key pair, use **Test position access**, then open **My positions → Sync positions**. Saved keys, successful reads, verified read-only permissions, and failures are separate states. Both exchanges show the last successful sync and its counts; sync does not start AI analysis. Failed or incomplete responses preserve existing positions. Imported exchange facts are protected; local stops, targets, and notes can be supplemented without changing exchange orders. Manual positions remain available without keys.

Binance integration has automated and mocked UI coverage, but **has not yet been validated against a real Binance account using an Enable Reading-only key**.

### Build a local package

```bash
pnpm desktop:build
```

Output goes to `apps/desktop/release/`. The package includes the frontend, Python runtime, dependencies, and SQLite; the Codex and Claude Code providers still need their installed CLIs. The current native package retains the name **AI Trade Helper**, although the interface is branded **txTrade**.

For the macOS Apple Silicon build:

```bash
open "apps/desktop/release/mac-arm64/AI Trade Helper.app"
```

macOS arm64 is the validated local target. The package uses ad-hoc signing; release signing/notarization and Windows/Linux distribution have not been validated.

### Product version and release notes

`version.json` is the product version source for the desktop, web, and Python packages. Desktop startup and packaging check version consistency first. Use **Settings → About AI Trade Helper** to see the installed version, and **Settings → Changelog** for its bundled notes in the selected language.

```bash
pnpm release:check
pnpm release:version
pnpm release:test
```

`release:version` without arguments synchronizes package metadata with the current product version. To prepare a new version, first write matching entries under `Unreleased` in [CHANGELOG.md](CHANGELOG.md) and [CHANGELOG.en.md](CHANGELOG.en.md), then run `pnpm release:prepare <version>`; stable versions use `x.y.z`, beta versions use `x.y.z-beta.N`. This prepares dated notes and package versions locally. It does not create tags, publish releases, or enable automatic updates. `pnpm release:check --require-release` checks that the current version has dated notes.

The existing `0.2.0` baseline remains unreleased. GitHub Actions now builds macOS arm64 test DMG/ZIP artifacts for PRs and manual runs. Matching version tags require Developer ID signing and notarization and produce a **draft** GitHub Release. The native update menu waits for active model work and backs up local data before installation; updates are disabled in development and test packages. Run `pnpm desktop:test-release` for local test installers.

## Architecture and local data

| Location | Responsibility |
| --- | --- |
| `apps/web/` | React/Vite workspace, charts, settings, and streaming discussions |
| `apps/api/` | FastAPI, market data, Python calculations, model providers, and background workers |
| `apps/desktop/` | Electron lifecycle, private loopback authorization, native appearance, and packaging |

The desktop API binds to loopback on a free port and requires an app-session token. SQLite + WAL provides one local personal workspace and shared worker storage; normal operation does not connect to PostgreSQL. This is not a multi-user or publicly hosted service.

On macOS, use **Settings → Open data directory** to find `data/trade_helper.sqlite3` under the app's user-data directory. Browser development defaults to the repository's `data/` directory; `APP_DATA_DIR` or `APP_DB_PATH` can override it.

App-managed keys and login material are encrypted with AES-256-GCM in SQLite. **The encryption master key is in the same database**, so a database copy contains recoverable credentials. No system keychain is used. Keep databases, backups, logs, exports, and `.env` private; do not commit or share them. Use the SQLite backup workflow rather than copying an active main database file without its WAL.

Local storage does not make inference offline: the chosen model provider receives the submitted market, position, and discussion context. Exchange reads and official-source collection also require internet access.

## Browser development

The browser workflow is for local development and debugging. From the repository root:

```bash
cp .env.example .env
cd apps/api
uv sync
cd ../web
pnpm install
cd ../..
bash scripts/dev.sh
```

Open `http://127.0.0.1:5173/`; FastAPI documentation is at `http://127.0.0.1:8000/docs`. The script starts the API, workers, and Vite; Ctrl+C stops that development group. Avoid starting a second instance on the same ports.

Use [`.env.example`](.env.example) for backend development configuration. The default browser API provider needs `OPENAI_API_KEY` and `OPENAI_MODEL` unless a different provider is configured. Desktop credentials belong in Settings; Binance credentials are not loaded from environment variables. Never put private keys in `VITE_*` variables, which are frontend-visible. Legacy `DATABASE_URL` is ignored by normal app startup.

## Verification

After installing the dependencies, run from the repository root:

```bash
node --test apps/web/tests/*.test.mjs
pnpm --dir apps/web lint
pnpm --dir apps/web build
pnpm desktop:test
pnpm release:test
pnpm release:check
pnpm check:bilingual
cd apps/api
uv run pytest
uv run ruff check src tests scripts
```

`check:bilingual` checks prompt/version consistency and paired language resources using isolated fixtures. Build and automated tests do not replace live provider/account acceptance or establish trading returns. Model or data-source failures are shown explicitly; a rules-only result is not presented as successful AI analysis.

Broad licensed crypto-news coverage, consensus forecasts as strategy evidence, automatic embedding/retrieval in the Agent workflow, and an actual execution-cost ledger remain future work. Cloud deployment is outside the current local-app scope.
