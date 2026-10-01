"""Localize known system failures without exposing upstream exception payloads.

Only fixed application messages and explicitly recognized exception names may
reach a saved report. Trading data and AI-generated prose never pass through
this module.
"""

from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError

from .credential_store import CredentialStoreError

SYSTEM_ERRORS = {
    "QUOTE_STALE": (
        "分析用報價快照超過 180 秒，請重新分析",
        "The analysis quote is more than 180 seconds old. Start a new analysis.",
    ),
    "CANDLES_STALE": (
        "已收盤 K 線資料過期，請重新分析",
        "The closed candles are out of date. Start a new analysis.",
    ),
    "CONTEXT_CANDLES_STALE": (
        "另一週期的已收盤 K 線過期，請重新分析",
        "The closed candles for the other timeframe are out of date. Start a new analysis.",
    ),
    "MARKET_DATA_UNAVAILABLE": (
        "Binance 行情暫時無法取得，請稍後重新分析。",
        "Binance market data is temporarily unavailable. Try the analysis again shortly.",
    ),
    "ANALYSIS_FAILED": (
        "無法完成分析，請檢查服務與行情資料後重新分析。",
        "The analysis could not be completed. Check the service and market data, then try again.",
    ),
}

# These are application-authored literals, not text from a provider's response.
# Preserve established Chinese messages while giving English jobs useful errors.
MODEL_MESSAGES = {
    "OPENAI_API_KEY and OPENAI_MODEL are required for Agent analysis": (
        "尚未設定 OPENAI_API_KEY 或 OPENAI_MODEL，無法產生 Agent 策略分析",
        "OPENAI_API_KEY or OPENAI_MODEL is not configured. Complete model setup before analyzing.",
    ),
    "APP_ANALYSIS_TIMEOUT_SECONDS 必須介於 30 與 900 秒。": (
        "APP_ANALYSIS_TIMEOUT_SECONDS 必須介於 30 與 900 秒。",
        "APP_ANALYSIS_TIMEOUT_SECONDS must be between 30 and 900 seconds.",
    ),
    "APP_CODEX_REASONING_EFFORT 不是支援的推理強度。": (
        "APP_CODEX_REASONING_EFFORT 不是支援的推理強度。",
        "APP_CODEX_REASONING_EFFORT is not a supported reasoning effort.",
    ),
    "APP_CLAUDE_CODE_EFFORT 不是支援的推理強度。": (
        "APP_CLAUDE_CODE_EFFORT 不是支援的推理強度。",
        "APP_CLAUDE_CODE_EFFORT is not a supported reasoning effort.",
    ),
    "Codex 沒有可用模型。": (
        "Codex 沒有可用模型。", "No Codex models are available.",
    ),
    "找不到 Codex。請先安裝 Codex CLI 或 ChatGPT 桌面應用程式。": (
        "找不到 Codex。請先安裝 Codex CLI 或 ChatGPT 桌面應用程式。",
        "Codex was not found. Install Codex CLI or the ChatGPT desktop app first.",
    ),
    "無法讀取本應用程式的 Codex 授權，請在設定重新登入。": (
        "無法讀取本應用程式的 Codex 授權，請在設定重新登入。",
        "This app could not read Codex authorization. Sign in again in Settings.",
    ),
    "Codex 連線已中斷，請重新連接。": (
        "Codex 連線已中斷，請重新連接。", "The Codex connection was interrupted. Reconnect.",
    ),
    "Codex 已停止，請重新連接。": (
        "Codex 已停止，請重新連接。", "Codex stopped. Reconnect.",
    ),
    "Codex 分析未完成，請檢查登入狀態與帳號額度後重試。": (
        "Codex 分析未完成，請檢查登入狀態與帳號額度後重試。",
        "Codex did not finish the analysis. Check sign-in status and account limits, then retry.",
    ),
    "Codex 沒有回傳分析報告。": (
        "Codex 沒有回傳分析報告。", "Codex did not return an analysis report.",
    ),
    "請先在設定連接 Codex 的 ChatGPT 帳號。": (
        "請先在設定連接 Codex 的 ChatGPT 帳號。",
        "Connect your ChatGPT account for Codex in Settings first.",
    ),
    "找不到 Claude Code。請先安裝 Claude Code CLI。": (
        "找不到 Claude Code。請先安裝 Claude Code CLI。",
        "Claude Code was not found. Install the Claude Code CLI first.",
    ),
    "請先在終端機執行 claude auth login 登入 Claude Code，再回到設定按「連線 Claude Code」。": (
        "請先在終端機執行 claude auth login 登入 Claude Code，再回到設定按「連線 Claude Code」。",
        "Run claude auth login in a terminal to sign in to Claude Code, then choose Connect Claude Code in Settings.",
    ),
    "Claude Code 暫時無法回應，請稍後重試。": (
        "Claude Code 暫時無法回應，請稍後重試。",
        "Claude Code is not responding. Try again shortly.",
    ),
    "無法讀取 Claude Code 登入狀態，請確認 CLI 版本。": (
        "無法讀取 Claude Code 登入狀態，請確認 CLI 版本。",
        "Claude Code sign-in status could not be read. Check the CLI version.",
    ),
    "Claude Code 連線已中斷，請重新分析。": (
        "Claude Code 連線已中斷，請重新分析。",
        "The Claude Code connection was interrupted. Start the analysis again.",
    ),
    "Claude Code 已停止，請確認登入狀態後重試。": (
        "Claude Code 已停止，請確認登入狀態後重試。",
        "Claude Code stopped. Check its sign-in status and retry.",
    ),
    "Claude Code 無法啟動分析，請確認版本與登入狀態。": (
        "Claude Code 無法啟動分析，請確認版本與登入狀態。",
        "Claude Code could not start the analysis. Check its version and sign-in status.",
    ),
    "Claude Code 分析未完成，請檢查登入狀態與帳號額度後重試。": (
        "Claude Code 分析未完成，請檢查登入狀態與帳號額度後重試。",
        "Claude Code did not finish the analysis. Check sign-in status and usage limits, then retry.",
    ),
    "Claude Code 沒有回傳分析報告。": (
        "Claude Code 沒有回傳分析報告。", "Claude Code did not return an analysis report.",
    ),
}

SAFE_EXCEPTION_NAMES = frozenset({
    "Exception", "ValueError", "RuntimeError", "TimeoutError", "TypeError", "KeyError",
    "OSError", "JSONDecodeError", "APITimeoutError", "APIConnectionError", "AuthenticationError",
    "RateLimitError", "ModelProviderError", "CodexError", "CodexTimeoutError", "ClaudeCodeError",
    "ClaudeCodeTimeoutError",
    "CredentialStoreError",
})

_ANALYSIS_STAGES = {
    "prompt": ("讀取分析設定", "loading analysis settings"),
    "macro": ("準備宏觀資料", "preparing macro context"),
    "candles": ("取得主週期 K 線", "fetching primary candles"),
    "context_candles": ("取得輔助週期 K 線", "fetching context candles"),
    "higher_candles": ("取得長週期 K 線", "fetching higher timeframe candles"),
    "quote": ("取得現價", "fetching the current price"),
    "tick_size": ("取得最小價格單位", "fetching price precision"),
    "events": ("讀取經濟事件", "loading economic events"),
    "derivatives": ("取得合約市場資料", "fetching derivatives context"),
    "news": ("讀取新聞依據", "loading news context"),
    "snapshot": ("建立行情快照", "building the market snapshot"),
    "preparation": ("計算分析指標", "preparing analysis indicators"),
    "model": ("產生 AI 分析", "generating AI analysis"),
    "report": ("整理分析報告", "building the analysis report"),
    "save_report": ("儲存分析報告", "saving the analysis report"),
}


def analysis_failure_message(stage: str, exc: Exception, output_locale: str = "zh-TW") -> str:
    """Give useful diagnostics without persisting an upstream exception message."""
    english = output_locale == "en-US"
    label = _ANALYSIS_STAGES.get(stage, ("分析流程", "analysis workflow"))[int(english)]
    name = type(exc).__name__
    if name not in SAFE_EXCEPTION_NAMES:
        name = "Exception"
    if english:
        return f"Analysis stopped while {label} ({name}). Try again; if it repeats, share the task ID."
    return f"分析在「{label}」時中斷（{name}）。請重新分析；若重複發生，請提供任務編號。"


def system_error_message(code: str, output_locale: str = "zh-TW") -> str:
    """Return a fixed localized message even for an unknown error code."""
    messages = SYSTEM_ERRORS.get(code, SYSTEM_ERRORS["ANALYSIS_FAILED"])
    return messages[1 if output_locale == "en-US" else 0]


def model_error_message(exc: Exception, output_locale: str = "zh-TW") -> str:
    """Expose only application literals, known validation details, or a safe type."""
    english = output_locale == "en-US"
    message = str(exc)
    if message in MODEL_MESSAGES:
        return MODEL_MESSAGES[message][1 if english else 0]
    if isinstance(exc, ValueError):
        # Reuse the existing application allowlist; do not add strategy checks.
        from .agent import _VALIDATION_ERRORS

        if message in _VALIDATION_ERRORS:
            prefix = "The model response could not be processed: " if english else "模型回應處理失敗："
            return prefix + message
    if english:
        if isinstance(exc, (TimeoutError, APITimeoutError)):
            return "The model analysis timed out. Check usage before starting a new analysis."
        if isinstance(exc, AuthenticationError):
            return "Model authorization failed. Reconnect the model in Settings and retry."
        if isinstance(exc, RateLimitError):
            return "The model is rate limited. Check account limits and retry later."
        if isinstance(exc, APIConnectionError):
            return "The model connection failed. Check the connection and retry."
        if isinstance(exc, CredentialStoreError):
            return "Local model credentials could not be read. Check your connection in Settings."
    name = type(exc).__name__
    if name not in SAFE_EXCEPTION_NAMES:
        name = "Exception"
    return (f"The model analysis could not be completed ({name}). Check model settings and retry."
            if english else f"模型工具流程失敗：{name}")
