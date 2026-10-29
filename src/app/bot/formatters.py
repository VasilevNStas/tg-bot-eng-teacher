"""HTML formatting for Telegram replies."""

from __future__ import annotations

from html import escape

from app.services.conversation.service import ConversationTurn

TELEGRAM_LIMIT = 4096
CORRECTIONS_LIMIT = 4000


def format_reply(turn: ConversationTurn, *, limit: int = TELEGRAM_LIMIT) -> str:
    text = escape(turn.reply, quote=False)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def format_corrections(
    turn: ConversationTurn,
    *,
    limit: int = CORRECTIONS_LIMIT,
) -> str | None:
    if not turn.corrections:
        return None

    opening = "<blockquote expandable>"
    closing = "</blockquote>"
    lines: list[str] = []

    for correction in turn.corrections:
        original = escape(correction.original, quote=False)
        corrected = escape(correction.corrected, quote=False)
        explanation = escape(correction.explanation_ru, quote=False)
        line = f"• <b>{original}</b> → <b>{corrected}</b>\n{explanation}"
        candidate = "\n".join([*lines, line])
        if len(opening) + len(candidate) + len(closing) > limit:
            break
        lines.append(line)

    if not lines:
        return None
    body = "\n".join(lines)
    return f"{opening}{body}{closing}"
