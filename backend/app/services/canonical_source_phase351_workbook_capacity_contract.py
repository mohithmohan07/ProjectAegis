"""Phase 3.5.1 provider-maximum capacity for the vendored workbook writer."""
from __future__ import annotations

from functools import wraps

from .. import config
from . import workbooks

_CONTRACT_VERSION = 1


def install() -> None:
    if getattr(workbooks, "_PHASE351_WORKBOOK_CAPACITY_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_vendor = workbooks._vendor
    workbooks._PHASE351_ORIGINAL_VENDOR = original_vendor

    @wraps(original_vendor)
    def vendor_provider_max():
        result = workbooks._PHASE351_ORIGINAL_VENDOR()
        import gpt_writer

        writer = gpt_writer.GPTWriter
        writer.MAX_MMD_CHARS = int(config.OPENAI_MAX_INPUT_CHARS)
        writer._MAX_OUT = int(config.OPENAI_MAX_OUTPUT_TOKENS)
        writer.MAX_PLANNER_OUTPUT_TOKENS = writer._MAX_OUT
        writer.MAX_BUILDER_OUTPUT_TOKENS = writer._MAX_OUT
        writer.MAX_TOPIC_OUTPUT_TOKENS = writer._MAX_OUT
        writer.MAX_SHELL_OUTPUT_TOKENS = writer._MAX_OUT

        if not getattr(writer, "_PHASE351_CHAT_WRAPPED", False):
            original_chat = writer._chat
            writer._PHASE351_ORIGINAL_CHAT = original_chat

            @wraps(original_chat)
            def chat_provider_max(
                self,
                system: str,
                user: str,
                *,
                max_tokens: int,
                purpose,
            ) -> str:
                effective = (
                    int(config.OPENAI_MAX_OUTPUT_TOKENS)
                    if config.OPENAI_PROVIDER_MAX_TOKENS
                    and config.use_live_generation()
                    else max_tokens
                )
                return writer._PHASE351_ORIGINAL_CHAT(
                    self,
                    system,
                    user,
                    max_tokens=effective,
                    purpose=purpose,
                )

            writer._chat = chat_provider_max
            writer._PHASE351_CHAT_WRAPPED = True
        return result

    workbooks._vendor = vendor_provider_max
    workbooks._PHASE351_WORKBOOK_CAPACITY_VERSION = _CONTRACT_VERSION
