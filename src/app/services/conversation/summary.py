"""Incremental conversation summaries.

The summarizer folds messages older than the active history window into a
rolling summary. It never holds a DB transaction while the LLM call is in
flight, and it treats a failed summary as a non-fatal event: the caller can
continue with the previous summary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.database import Database
from app.db.enums import MessageRole
from app.db.models import Conversation
from app.db.models import Message as DbMessage
from app.llm.errors import LLMError
from app.llm.models import Route, RouteSpec
from app.llm.port import LLMClient, LLMResponse
from app.llm.port import Message as LlmMessage
from app.llm.tokens import estimate_messages_tokens
from app.services.prompts.registry import PromptRegistry
from app.services.usage.service import DailyBudgetExceeded, UsageService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SummaryState:
    """Current summary and the last message folded into it."""

    text: str | None
    summarized_until_message_id: int | None
    updated: bool = False


class ConversationSummarizer:
    """Build and persist a rolling summary for one conversation."""

    def __init__(
        self,
        database: Database,
        llm: LLMClient,
        usage: UsageService,
        prompts: PromptRegistry,
        *,
        routes: Mapping[Route, RouteSpec],
        history_window: int,
        batch_size: int = 12,
        max_batch_size: int = 50,
        message_max_chars: int = 800,
        summary_max_chars: int = 2000,
        max_input_tokens: int = 4000,
    ) -> None:
        if history_window < 0:
            raise ValueError("history_window must not be negative")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if max_batch_size <= 0:
            raise ValueError("max_batch_size must be positive")
        if message_max_chars <= 0:
            raise ValueError("message_max_chars must be positive")
        if summary_max_chars <= 0:
            raise ValueError("summary_max_chars must be positive")
        if max_input_tokens <= 0:
            raise ValueError("max_input_tokens must be positive")

        self._database = database
        self._llm = llm
        self._usage = usage
        self._prompts = prompts
        self._route = routes[Route.SUMMARY]
        self._history_window = history_window
        self._batch_size = batch_size
        self._max_batch_size = max_batch_size
        self._message_max_chars = message_max_chars
        self._summary_max_chars = summary_max_chars
        self._max_input_tokens = max_input_tokens

    async def maybe_rollup(self, conversation: Conversation) -> SummaryState:
        """Summarize old messages if enough of them accumulated."""
        state = _state(conversation)
        watermark = conversation.summarized_until_message_id or 0
        rows = await self._unsummarized(conversation.id, watermark)
        if not rows:
            return state

        candidates = rows[: -self._history_window] if self._history_window > 0 else rows

        if len(candidates) < self._batch_size:
            return state
        return await self._summarize(conversation, candidates[: self._max_batch_size])

    async def finalize(self, conversation: Conversation) -> SummaryState:
        """Fold the remaining messages in before a conversation is closed."""
        state = _state(conversation)
        watermark = conversation.summarized_until_message_id or 0
        rows = await self._unsummarized(conversation.id, watermark)
        if not rows:
            return state

        # A short conversation without an existing summary is not worth a call.
        if conversation.summary is None and len(rows) < self._batch_size:
            return state

        return await self._summarize(conversation, rows[: self._max_batch_size])

    async def _summarize(
        self,
        conversation: Conversation,
        candidates: Sequence[DbMessage],
    ) -> SummaryState:
        """Call the summary route and persist the new summary atomically."""
        state = _state(conversation)
        if not candidates:
            return state

        template = self._prompts.summary()
        previous = (conversation.summary or "").strip()[: self._summary_max_chars]
        fitted = self._fit_messages(template.content, previous, candidates)
        if fitted is None:
            logger.warning(
                "summary_skipped",
                conversation_id=conversation.id,
                reason="input_too_long",
            )
            return state

        messages, kept = fitted
        estimated = estimate_messages_tokens(messages) + self._route.max_tokens
        try:
            await self._usage.ensure_available(
                conversation.user_id,
                estimated_tokens=estimated,
            )
        except DailyBudgetExceeded:
            logger.warning(
                "summary_skipped",
                conversation_id=conversation.id,
                reason="budget",
            )
            return state

        try:
            response = await self._complete(messages)
        except LLMError as exc:
            logger.warning(
                "summary_failed",
                conversation_id=conversation.id,
                error=str(exc),
            )
            return state

        text = response.content.strip()
        if not text:
            logger.warning(
                "summary_skipped",
                conversation_id=conversation.id,
                reason="empty",
            )
            return state
        text = text[: self._summary_max_chars].rstrip()

        last_id = kept[-1].id
        async with self._database.session() as session:
            stored = await session.get(Conversation, conversation.id)
            if stored is None:
                logger.warning(
                    "summary_failed",
                    conversation_id=conversation.id,
                    reason="conversation_missing",
                )
                return state
            stored.summary = text
            stored.summarized_until_message_id = last_id
            session.add(
                self._usage.build_entry(
                    user_id=conversation.user_id,
                    conversation_id=conversation.id,
                    model=response.model,
                    prompt_version=f"summary:{template.version}",
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost_usd=response.cost_usd,
                    latency_ms=response.latency_ms,
                )
            )

        logger.info(
            "conversation_summarized",
            conversation_id=conversation.id,
            messages=len(kept),
            dropped=len(candidates) - len(kept),
            watermark=last_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=str(response.cost_usd),
            latency_ms=response.latency_ms,
        )
        return SummaryState(
            text=text,
            summarized_until_message_id=last_id,
            updated=True,
        )

    async def _complete(self, messages: Sequence[LlmMessage]) -> LLMResponse:
        """Call the summary route with its fallback, temperature and token cap."""
        route = self._route
        if route.fallback:
            response = await self._llm.complete(
                messages,
                model=route.model,
                fallback_model=route.fallback,
                temperature=route.temperature,
                max_tokens=route.max_tokens,
            )
        else:
            response = await self._llm.complete(
                messages,
                model=route.model,
                temperature=route.temperature,
                max_tokens=route.max_tokens,
            )
        logger.info(
            "llm_call",
            route=route.name.value,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=str(response.cost_usd),
            latency_ms=response.latency_ms,
        )
        return response

    async def _unsummarized(
        self,
        conversation_id: int,
        watermark: int,
    ) -> list[DbMessage]:
        """Load messages after the watermark in chronological order."""
        async with self._database.session() as session:
            rows = await session.scalars(
                select(DbMessage)
                .where(
                    DbMessage.conversation_id == conversation_id,
                    DbMessage.id > watermark,
                    DbMessage.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
                )
                .order_by(DbMessage.id)
            )
            return list(rows)

    def _fit_messages(
        self,
        template: str,
        previous: str,
        candidates: Sequence[DbMessage],
    ) -> tuple[list[LlmMessage], list[DbMessage]] | None:
        """Shrink message excerpts until the summary prompt fits the input budget."""
        per_char = self._message_max_chars
        while per_char >= 100:
            messages = self._build_messages(template, previous, candidates, per_char)
            if estimate_messages_tokens(messages) <= self._max_input_tokens:
                return messages, list(candidates)
            per_char //= 2

        kept = list(candidates)
        while kept:
            messages = self._build_messages(template, previous, kept, 100)
            if estimate_messages_tokens(messages) <= self._max_input_tokens:
                return messages, kept
            kept = kept[1:]
        return None

    @staticmethod
    def _build_messages(
        template: str,
        previous: str,
        candidates: Sequence[DbMessage],
        per_char: int,
    ) -> list[LlmMessage]:
        """Render a data-only summary request as JSON."""
        payload = {
            "previous_summary": previous,
            "messages": [
                {
                    "role": message.role.value,
                    "content": message.content[:per_char],
                }
                for message in candidates
            ],
        }
        return [
            LlmMessage(role="system", content=template),
            LlmMessage(
                role="user",
                content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        ]


def _state(conversation: Conversation) -> SummaryState:
    """Snapshot the current summary fields of a detached conversation."""
    return SummaryState(
        text=conversation.summary,
        summarized_until_message_id=conversation.summarized_until_message_id,
    )
