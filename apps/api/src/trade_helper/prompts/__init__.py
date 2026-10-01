"""Shared bilingual prompt registry for every local model provider."""

from .contracts import SUPPORTED_LOCALES, Locale, PromptBundle, PromptInputs, PromptTask
from .registry import news_questions, resolve_prompt, task_prompt_version, validate_registry

__all__ = [
    "SUPPORTED_LOCALES",
    "Locale",
    "PromptBundle",
    "PromptInputs",
    "PromptTask",
    "news_questions",
    "resolve_prompt",
    "task_prompt_version",
    "validate_registry",
]
