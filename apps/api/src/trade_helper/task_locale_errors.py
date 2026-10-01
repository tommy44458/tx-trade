"""Known, safe task errors rendered in the requested language without rewriting history."""

ERROR_TEXT = {
    "DISCUSSION_RESULT_UNKNOWN": (
        "模型回覆中斷，無法確認這次呼叫的用量；請檢查後手動重試。",
        "The model reply was interrupted; usage is unknown. Check usage before retrying manually.",
    ),
    "DISCUSSION_TIMEOUT": (
        "模型回覆逾時；請檢查用量後手動重試。",
        "The model reply timed out. Check usage before retrying manually.",
    ),
    "DISCUSSION_AUTH_REQUIRED": (
        "模型授權已失效，請在設定重新連接後重試。",
        "Model authorization expired. Reconnect in Settings before retrying.",
    ),
    "DISCUSSION_RATE_LIMITED": (
        "模型目前受到用量限制，請稍後手動重試。",
        "The model is currently rate limited. Retry manually later.",
    ),
    "DISCUSSION_CONNECTION_FAILED": (
        "模型連線中斷，請檢查連線與用量後手動重試。",
        "The model connection was interrupted. Check the connection and usage before retrying manually.",
    ),
    "DISCUSSION_FAILED": (
        "無法完成這次追問，請檢查模型設定與授權後手動重試。",
        "The discussion reply could not be completed. Check model settings and authorization before retrying.",
    ),
    "MACRO_LEASE_EXPIRED": (
        "宏觀解讀程序已中斷；可按重試繼續。",
        "The macro interpretation was interrupted. Retry to continue.",
    ),
    "MACRO_MODEL_UNAUTHORIZED": (
        "模型授權已失效，請在設定重新連接帳號後重試。",
        "Model authorization expired. Reconnect your account in Settings before retrying.",
    ),
    "MACRO_MODEL_UNCONFIGURED": (
        "尚未連接可用的 AI 模型；請完成模型設定後重試宏觀解讀。",
        "No AI model is connected. Complete model setup before retrying the macro interpretation.",
    ),
    "MACRO_MODEL_TIMEOUT": (
        "AI 宏觀解讀逾時，可稍後重試；不會以規則代替 AI 解讀。",
        "The AI macro interpretation timed out. Retry later; no rule-based substitute was generated.",
    ),
    "MACRO_MODEL_UNAVAILABLE": (
        "AI 模型暫時不可用，請檢查授權與額度後重試。",
        "The AI model is temporarily unavailable. Check authorization and usage limits before retrying.",
    ),
    "MACRO_RESPONSE_FORMAT": (
        "AI 未回傳完整宏觀解讀，可按重試重新產生。",
        "The AI did not return a complete macro interpretation. Retry to generate it again.",
    ),
    "MACRO_MODEL_FAILED": (
        "AI 宏觀解讀未完成，可稍後重試。",
        "The AI macro interpretation could not be completed. Retry later.",
    ),
    "MACRO_TRANSLATION_RESULT_UNKNOWN": (
        "翻譯程序已中斷，用量無法確認；請檢查後手動重試。",
        "Translation was interrupted; usage is unknown. Check usage before retrying manually.",
    ),
    "MACRO_TRANSLATION_TIMEOUT": (
        "宏觀解讀翻譯逾時；請檢查用量後手動重試。",
        "The macro translation timed out. Check usage before retrying manually.",
    ),
    "MACRO_TRANSLATION_FORMAT": (
        "翻譯回覆格式不完整，原解讀保持原樣；可手動重試。",
        "The translation response was incomplete. The original interpretation is unchanged; retry manually.",
    ),
    "MACRO_TRANSLATION_FAILED": (
        "無法翻譯已存解讀；請檢查模型設定與授權後手動重試。",
        "The saved interpretation could not be translated. Check model settings and authorization before retrying.",
    ),
}


def localized_task_error(code: str, locale: str, fallback: str) -> str:
    values = ERROR_TEXT.get(code)
    return values[1 if locale == "en-US" else 0] if values else fallback
