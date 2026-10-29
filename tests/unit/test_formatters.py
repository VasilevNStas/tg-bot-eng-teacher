from __future__ import annotations

from decimal import Decimal

from app.bot.formatters import CORRECTIONS_LIMIT, format_corrections, format_reply
from app.services.conversation.service import ConversationTurn
from app.services.correction.schema import CorrectionItem


def make_turn(
    *,
    reply: str = "Nice work!",
    corrections: tuple[CorrectionItem, ...] = (),
) -> ConversationTurn:
    return ConversationTurn(
        conversation_id=1,
        user_message_id=1,
        assistant_message_id=2,
        reply=reply,
        corrections=corrections,
        new_vocabulary=(),
        prompt_version="abc123",
        model="fake/model",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0"),
    )


def make_correction(
    original: str = "I go",
    corrected: str = "I went",
    explanation: str = "Past Simple.",
) -> CorrectionItem:
    return CorrectionItem(
        original=original,
        corrected=corrected,
        category="tense",
        explanation_ru=explanation,
    )


def test_reply_is_escaped() -> None:
    turn = make_turn(reply="<b>Hello</b> & <script>")

    assert format_reply(turn) == "&lt;b&gt;Hello&lt;/b&gt; &amp; &lt;script&gt;"


def test_reply_is_truncated() -> None:
    turn = make_turn(reply="x" * 5000)

    text = format_reply(turn)

    assert len(text) == 4096
    assert text.endswith("…")


def test_no_corrections_means_no_block() -> None:
    assert format_corrections(make_turn()) is None


def test_corrections_are_an_expandable_blockquote() -> None:
    turn = make_turn(corrections=(make_correction(),))

    text = format_corrections(turn)

    assert text is not None
    assert text.startswith("<blockquote expandable>")
    assert text.endswith("</blockquote>")
    assert "<b>I go</b> → <b>I went</b>" in text
    assert "Past Simple." in text


def test_corrections_are_escaped() -> None:
    turn = make_turn(corrections=(make_correction(original="<i>", corrected="<b>"),))

    text = format_corrections(turn)

    assert text is not None
    assert "<i>" not in text
    assert "&lt;i&gt;" in text


def test_corrections_fit_the_limit() -> None:
    corrections = tuple(make_correction(explanation="explanation " * 50) for _ in range(50))

    text = format_corrections(make_turn(corrections=corrections))

    assert text is not None
    assert len(text) <= CORRECTIONS_LIMIT
    assert text.endswith("</blockquote>")
