"""HTML formatting for Telegram replies.

Telegram accepts a small HTML subset, so every user- or LLM-provided string is
escaped with ``quote=False`` before it is embedded. All builders also respect
Telegram's hard message limit (4096 characters) and truncate gracefully instead
of letting the API reject the whole message.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from html import escape

from app.db.models import Vocabulary
from app.services.conversation.service import ConversationTurn
from app.services.privacy.service import UserDataSummary
from app.services.stats.service import StatsReport
from app.services.vocabulary.srs import ReviewCard
from app.texts import ru

TELEGRAM_LIMIT = 4096
# Correction blocks are kept below the hard limit to leave room for the reply.
CORRECTIONS_LIMIT = 4000


def format_reply(turn: ConversationTurn, *, limit: int = TELEGRAM_LIMIT) -> str:
    """Escape the assistant reply and cut it to the Telegram limit."""
    text = escape(turn.reply, quote=False)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def format_corrections(
    turn: ConversationTurn,
    *,
    limit: int = CORRECTIONS_LIMIT,
) -> str | None:
    """Render corrections as an expandable blockquote, or None when empty.

    Lines are added until the *whole* block would exceed the limit, so we never
    split an individual correction in half.
    """
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


def format_correction_report(
    turn: ConversationTurn,
    *,
    limit: int = TELEGRAM_LIMIT,
) -> str:
    """Render the full post-reply report: reply, corrections, new words."""
    reply = _shorten(escape(turn.reply, quote=False), max(1, limit - 80))
    lines = [reply, "", ru.CORRECTION_HEADER]

    if turn.corrections:
        for correction in turn.corrections:
            category = (
                ru.ERROR_CATEGORY_RU.get(correction.category.value) or correction.category.value
            )
            line = (
                f"• <b>{escape(correction.original, quote=False)}</b> → "
                f"<b>{escape(correction.corrected, quote=False)}</b>\n"
                f"<i>{escape(category, quote=False)}</i>\n"
                f"{escape(correction.explanation_ru, quote=False)}"
            )
            if not _append_within(lines, line, limit):
                lines.append("…")
                return "\n".join(lines)
    else:
        lines.append(ru.CORRECTION_NONE)

    if turn.new_vocabulary:
        if not _append_within(lines, "", limit):
            return "\n".join(lines)
        if not _append_within(lines, ru.NEW_VOCABULARY_HEADER, limit):
            return "\n".join(lines)
        for word in turn.new_vocabulary:
            line = (
                f"• <b>{escape(word.term, quote=False)}</b> — "
                f"{escape(word.translation_ru, quote=False)}"
            )
            if word.context:
                line += f"\n<i>{escape(_shorten(word.context, 160), quote=False)}</i>"
            if not _append_within(lines, line, limit):
                lines.append("…")
                break

    return "\n".join(lines)


def format_review_card(card: ReviewCard) -> str:
    """Render one saved-vocabulary card with its buttons' prompt."""
    lines = [
        ru.REVIEW_HEADER,
        "",
        f"<b>{escape(card.term, quote=False)}</b>",
        escape(card.translation, quote=False),
    ]
    if card.context:
        lines.extend(["", f"<i>{escape(_shorten(card.context, 160), quote=False)}</i>"])
    lines.extend(["", ru.REVIEW_PROMPT])
    return "\n".join(lines)


def format_stats(report: StatsReport) -> str:
    """Render the read-only statistics report in fixed section order."""
    lines = [ru.STATS_HEADER, "", ru.STATS_PRACTICE_HEADER]
    if report.attempts_total:
        lines.append(
            ru.STATS_ATTEMPTS.format(
                total=report.attempts_total,
                correct=report.correct,
                accuracy=report.accuracy,
            )
        )
        lines.append(
            ru.STATS_BREAKDOWN.format(
                partial=report.partial,
                incorrect=report.incorrect,
                sessions=report.completed_sessions,
            )
        )
        if report.best_streak:
            lines.append(ru.STATS_STREAK.format(streak=report.best_streak))
    else:
        lines.append(ru.STATS_NO_PRACTICE)

    lines.extend(["", ru.STATS_WEAK_TOPICS_HEADER])
    if report.weak_topics:
        lines.extend(
            ru.STATS_TOPIC_ITEM.format(
                title=escape(topic.title, quote=False),
                accuracy=topic.accuracy,
                attempts=topic.attempts,
            )
            for topic in report.weak_topics
        )
    else:
        lines.append(ru.STATS_NO_TOPICS)

    lines.extend(
        [
            "",
            ru.STATS_VOCAB_HEADER,
            ru.STATS_VOCAB_TOTAL.format(
                total=report.vocabulary_total,
                due=report.due_count,
            ),
        ]
    )
    if report.weak_words:
        lines.extend(["", ru.STATS_WEAK_WORDS_HEADER])
        lines.extend(
            ru.STATS_WEAK_WORD_ITEM.format(
                term=escape(word.term, quote=False),
                lapses=word.lapses,
            )
            for word in report.weak_words
        )
    else:
        lines.extend(["", ru.STATS_NO_WEAK_WORDS])

    lines.extend(["", ru.STATS_WORDS_HEADER])
    if report.word_attempts:
        lines.append(
            ru.STATS_WORDS_SUMMARY.format(
                sessions=report.word_sessions,
                attempts=report.word_attempts,
                correct=report.word_correct,
                accuracy=report.word_accuracy,
            )
        )
        lines.append(ru.STATS_WORDS_SEEN.format(seen=report.bank_words_seen))
    else:
        lines.append(ru.STATS_WORDS_NO_DATA)

    if report.weak_bank_words:
        lines.extend(["", ru.STATS_WEAK_BANK_WORDS_HEADER])
        lines.extend(
            ru.STATS_WEAK_BANK_WORD_ITEM.format(
                term=escape(word.term, quote=False),
                wrong=word.wrong,
                attempts=word.attempts,
            )
            for word in report.weak_bank_words
        )
    else:
        lines.extend(["", ru.STATS_NO_WEAK_BANK_WORDS])

    lines.extend(["", ru.STATS_ERRORS_HEADER])
    if report.error_categories:
        lines.extend(
            ru.STATS_ERROR_ITEM.format(
                category=escape(
                    ru.ERROR_CATEGORY_RU.get(item.category.value) or item.category.value,
                    quote=False,
                ),
                count=item.count,
            )
            for item in report.error_categories
        )
    else:
        lines.append(ru.STATS_NO_ERRORS)

    return "\n".join(lines)


def format_vocabulary(
    words: Sequence[Vocabulary],
    *,
    limit: int = TELEGRAM_LIMIT,
) -> str:
    """Render saved vocabulary, truncating whole entries to fit the limit."""
    lines = [ru.VOCAB_HEADER.format(count=len(words))]
    for number, word in enumerate(words, start=1):
        line = ru.VOCAB_ITEM.format(
            number=number,
            term=escape(word.term, quote=False),
            translation=escape(word.translation, quote=False),
        )
        if word.context:
            line += "\n" + ru.VOCAB_CONTEXT.format(
                context=escape(_shorten(word.context, 120), quote=False)
            )
        if not _append_within(lines, line, limit):
            lines.append("…")
            break
    return "\n".join(lines)


def format_about_me(summary: UserDataSummary, retention_days: int) -> str:
    """Render the user's stored profile and row counts, never message texts."""
    quiet = ru.QUIET_NONE
    if summary.quiet_hours_start and summary.quiet_hours_end:
        quiet = f"{summary.quiet_hours_start}-{summary.quiet_hours_end}"

    retention = (
        ru.ABOUT_ME_RETENTION.format(days=retention_days)
        if retention_days > 0
        else ru.ABOUT_ME_NO_RETENTION
    )
    username = f"@{escape(summary.username, quote=False)}" if summary.username else "—"
    direction = (
        ru.WORDS_DIRECTION_LABELS.get(summary.card_direction, summary.card_direction)
        if summary.card_direction
        else "—"
    )

    return ru.ABOUT_ME.format(
        tg_id=summary.tg_id,
        username=username,
        level=summary.level.value,
        timezone=escape(summary.timezone, quote=False),
        quiet=quiet,
        created=_date(summary.created_at),
        last_seen=_date(summary.last_seen_at),
        card_size=summary.card_size if summary.card_size is not None else "—",
        direction=direction,
        conversations=summary.conversations,
        messages=summary.messages,
        corrections=summary.corrections,
        vocabulary=summary.vocabulary,
        sessions=summary.sessions,
        attempts=summary.attempts,
        word_progress=summary.word_progress,
        reminders=summary.reminders,
        usage=summary.usage,
        retention=retention,
    )


def _date(value: datetime | None) -> str:
    """Format a datetime as a short date, or a dash when it is missing."""
    return value.strftime("%d.%m.%Y") if value else "—"


def _append_within(lines: list[str], line: str, limit: int) -> bool:
    """Append a line only if the joined text stays within ``limit``."""
    candidate = "\n".join([*lines, line])
    if len(candidate) > limit:
        return False
    lines.append(line)
    return True


def _shorten(text: str, limit: int) -> str:
    """Cut text to ``limit`` characters, replacing the last one with ellipsis."""
    return text if len(text) <= limit else text[: limit - 1] + "…"
