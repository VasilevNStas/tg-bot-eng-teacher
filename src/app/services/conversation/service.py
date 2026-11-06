"""One conversational turn from input to persisted response.

The service owns the whole turn:

1. find/create the conversation and load the recent history;
2. build the prompt (system prompt + scenario + history);
3. check the user's daily token budget;
4. call the LLM (plain text or structured JSON);
5. persist both messages, corrections, new vocabulary and usage in one DB
   transaction.

Handlers receive a ``ConversationTurn`` with everything needed for the reply.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.database import Database
from app.db.enums import ConversationMode, MessageRole
from app.db.models import Conversation, User
from app.db.models import Correction as DbCorrection
from app.db.models import Message as DbMessage
from app.db.repositories.vocabulary import VocabularyRepository
from app.llm.errors import LLMInvalidResponse
from app.llm.models import Route, RouteSpec
from app.llm.port import LLMClient, LLMResponse
from app.llm.port import Message as LlmMessage
from app.llm.pricing import estimate_route_cost_usd
from app.services.conversation.summary import ConversationSummarizer
from app.services.correction.schema import (
    CorrectionItem,
    CorrectionPayload,
    NewVocabularyItem,
)
from app.services.correction.validation import sanitize_payload
from app.services.prompts.builder import PromptBuilder, PromptContext
from app.services.prompts.registry import PromptTemplate
from app.services.usage.service import UsageService

logger = get_logger(__name__)

# These modes ask the LLM for a JSON payload with corrections and vocabulary.
# Drill/roleplay replies are plain text.
STRUCTURED_MODES = frozenset({ConversationMode.CHAT, ConversationMode.CORRECTION})

_ROUTE_BY_MODE: dict[ConversationMode, Route] = {
    ConversationMode.CHAT: Route.CHAT,
    ConversationMode.CORRECTION: Route.CORRECTION,
    ConversationMode.DRILL: Route.CHAT,
    ConversationMode.ROLEPLAY: Route.ROLEPLAY,
}


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """Everything a handler needs after one completed turn."""

    conversation_id: int
    user_message_id: int
    assistant_message_id: int
    reply: str
    corrections: tuple[CorrectionItem, ...]
    new_vocabulary: tuple[NewVocabularyItem, ...]
    prompt_version: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    conversation_ended: bool = False


@dataclass(frozen=True, slots=True)
class NewConversationResult:
    """What ``/new`` closed and how many summaries were written."""

    closed: int
    summarized: int
    had_roleplay: bool


class ConversationService:
    """Run a single LLM-supported conversation turn."""

    def __init__(
        self,
        database: Database,
        llm: LLMClient,
        usage: UsageService,
        builder: PromptBuilder,
        *,
        routes: Mapping[Route, RouteSpec],
        summarizer: ConversationSummarizer | None = None,
        idle_timeout: timedelta = timedelta(minutes=30),
        repair_attempts: int = 1,
        max_conversation_messages: int = 200,
    ) -> None:
        if not routes:
            raise ValueError("at least one LLM route is required")
        if max_conversation_messages <= 0:
            raise ValueError("max_conversation_messages must be positive")
        self._database = database
        self._llm = llm
        self._usage = usage
        self._builder = builder
        self._routes = routes
        self._summarizer = summarizer or ConversationSummarizer(
            database,
            llm,
            usage,
            builder.registry,
            routes=routes,
            history_window=builder.history_window,
        )
        self._idle_timeout = idle_timeout
        self._repair_attempts = max(repair_attempts, 0)
        self._max_conversation_messages = max_conversation_messages

    async def respond(
        self,
        user: User,
        text: str,
        *,
        mode: ConversationMode = ConversationMode.CHAT,
        update_id: int | None = None,
        scenario: PromptTemplate | None = None,
    ) -> ConversationTurn:
        """Process one user message and return the assistant reply."""
        topic = scenario.id if scenario is not None else None
        conversation, history = await self._prepare(user, mode, topic)
        summary = await self._summarizer.maybe_rollup(conversation)
        conversation.summary = summary.text
        conversation.summarized_until_message_id = summary.summarized_until_message_id

        prompt = self._builder.build(
            PromptContext(
                level=user.level,
                mode=mode,
                user_input=text,
                profile=user.profile,
                summary=conversation.summary,
                history=history,
                native_language=user.native_lang,
                scenario=scenario,
            )
        )

        route = self._routes[_ROUTE_BY_MODE.get(mode, Route.CHAT)]
        structured = mode in STRUCTURED_MODES
        schema = CorrectionPayload if structured else None

        # Refuse before spending money if this call would exceed either budget.
        await self._usage.ensure_available(
            user.id,
            estimated_tokens=prompt.estimated_input_tokens + route.max_tokens,
        )
        await self._usage.ensure_global_available(
            estimated_cost_usd=estimate_route_cost_usd(
                route,
                prompt.estimated_input_tokens,
            )
        )

        response = await self._complete_with_repair(prompt.messages, route, schema)
        payload = sanitize_payload(self._payload(response, structured), text)

        user_message_id, assistant_message_id = await self._persist(
            user=user,
            conversation_id=conversation.id,
            user_text=text,
            payload=payload,
            response=response,
            prompt_version=prompt.prompt_version,
            update_id=update_id,
        )
        conversation_ended = await self._maybe_close_at_limit(conversation)

        return ConversationTurn(
            conversation_id=conversation.id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            reply=payload.reply,
            corrections=tuple(payload.corrections),
            new_vocabulary=tuple(payload.new_vocabulary),
            prompt_version=prompt.prompt_version,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=response.cost_usd,
            conversation_ended=conversation_ended,
        )

    async def start_roleplay(self, user_id: int, scenario_id: str) -> None:
        """Start a roleplay scenario, closing other active scenarios.

        Starting the same scenario twice is a no-op.
        """
        now = datetime.now(UTC)
        closed: list[Conversation] = []
        async with self._database.session() as session:
            active = await session.scalars(
                select(Conversation).where(
                    Conversation.user_id == user_id,
                    Conversation.mode == ConversationMode.ROLEPLAY,
                    Conversation.ended_at.is_(None),
                )
            )
            same = False
            for conversation in active:
                if conversation.topic == scenario_id:
                    same = True
                else:
                    conversation.ended_at = now
                    closed.append(conversation)
            if not same:
                session.add(
                    Conversation(
                        user_id=user_id,
                        mode=ConversationMode.ROLEPLAY,
                        topic=scenario_id,
                        started_at=now,
                    )
                )
        for conversation in closed:
            await self._summarizer.finalize(conversation)
            logger.info(
                "conversation_closed",
                conversation_id=conversation.id,
                reason="roleplay_switch",
            )

    async def stop_roleplay(self, user_id: int) -> str | None:
        """Close the newest active scenario and return its id."""
        now = datetime.now(UTC)
        async with self._database.session() as session:
            conversation = await session.scalar(
                select(Conversation)
                .where(
                    Conversation.user_id == user_id,
                    Conversation.mode == ConversationMode.ROLEPLAY,
                    Conversation.ended_at.is_(None),
                )
                .order_by(Conversation.started_at.desc())
                .limit(1)
            )
            if conversation is None:
                return None
            conversation.ended_at = now
            topic = conversation.topic
        await self._summarizer.finalize(conversation)
        logger.info(
            "conversation_closed",
            conversation_id=conversation.id,
            reason="roleplay_stop",
        )
        return topic

    async def start_new(self, user_id: int) -> NewConversationResult:
        """Close every active conversation and finalize their summaries."""
        now = datetime.now(UTC)
        async with self._database.session() as session:
            conversations = list(
                await session.scalars(
                    select(Conversation).where(
                        Conversation.user_id == user_id,
                        Conversation.ended_at.is_(None),
                    )
                )
            )
            for conversation in conversations:
                conversation.ended_at = now

        summarized = 0
        had_roleplay = False
        for conversation in conversations:
            had_roleplay = had_roleplay or conversation.mode is ConversationMode.ROLEPLAY
            state = await self._summarizer.finalize(conversation)
            if state.updated:
                summarized += 1
            logger.info(
                "conversation_closed",
                conversation_id=conversation.id,
                reason="new",
            )
        return NewConversationResult(
            closed=len(conversations),
            summarized=summarized,
            had_roleplay=had_roleplay,
        )

    async def active_roleplay(self, user_id: int) -> str | None:
        """Return the active scenario id, if any."""
        async with self._database.session() as session:
            return await session.scalar(
                select(Conversation.topic)
                .where(
                    Conversation.user_id == user_id,
                    Conversation.mode == ConversationMode.ROLEPLAY,
                    Conversation.ended_at.is_(None),
                )
                .order_by(Conversation.started_at.desc())
                .limit(1)
            )

    async def _complete_with_repair(
        self,
        messages: Sequence[LlmMessage],
        route: RouteSpec,
        schema: type[CorrectionPayload] | None,
    ) -> LLMResponse:
        """Call the route once, then try one structured-output repair."""
        try:
            return await self._complete(route, messages, schema)
        except LLMInvalidResponse as exc:
            if schema is None or self._repair_attempts < 1:
                raise
            logger.warning(
                "llm_repair",
                route=route.name.value,
                model=route.model,
                error=str(exc),
            )
            return await self._complete(route, self._repair_messages(messages, exc), schema)

    async def _complete(
        self,
        route: RouteSpec,
        messages: Sequence[LlmMessage],
        schema: type[CorrectionPayload] | None,
    ) -> LLMResponse:
        """Call one route with its model, temperature and token cap."""
        if route.fallback:
            response = await self._llm.complete(
                messages,
                model=route.model,
                fallback_model=route.fallback,
                temperature=route.temperature,
                max_tokens=route.max_tokens,
                response_schema=schema,
            )
        else:
            response = await self._llm.complete(
                messages,
                model=route.model,
                temperature=route.temperature,
                max_tokens=route.max_tokens,
                response_schema=schema,
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

    @staticmethod
    def _repair_messages(
        messages: Sequence[LlmMessage],
        error: LLMInvalidResponse,
    ) -> list[LlmMessage]:
        """Ask the model to repeat its answer as valid JSON after a schema error."""
        instruction = (
            "Your previous answer could not be parsed. Return ONLY one JSON object "
            "matching the required schema, with no prose and no code fences. "
            f"Validation error: {error}"
        )
        repair = list(messages)
        if error.content:
            repair.append(LlmMessage(role="assistant", content=error.content))
        repair.append(LlmMessage(role="user", content=instruction))
        return repair

    @staticmethod
    def _payload(response: LLMResponse, structured: bool) -> CorrectionPayload:
        """Normalize the LLM response into one payload type.

        Plain-text modes are wrapped with empty corrections/vocabulary so the
        caller does not need two code paths.
        """
        if not structured:
            return CorrectionPayload(
                reply=response.content,
                corrections=[],
                new_vocabulary=[],
            )
        if response.parsed is None:
            raise LLMInvalidResponse("structured mode returned no parsed payload")
        return CorrectionPayload.model_validate(response.parsed)

    async def _maybe_close_at_limit(self, conversation: Conversation) -> bool:
        """Close a conversation that reached the emergency message cap."""
        async with self._database.session() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(DbMessage)
                .where(DbMessage.conversation_id == conversation.id)
            )
        total = int(count or 0)
        if total < self._max_conversation_messages:
            return False

        state = await self._summarizer.finalize(conversation)
        conversation.summary = state.text
        conversation.summarized_until_message_id = state.summarized_until_message_id
        async with self._database.session() as session:
            stored = await session.get(Conversation, conversation.id)
            if stored is not None and stored.ended_at is None:
                stored.ended_at = datetime.now(UTC)
        logger.info(
            "conversation_closed",
            conversation_id=conversation.id,
            reason="limit",
            messages=total,
        )
        return True

    async def _prepare(
        self,
        user: User,
        mode: ConversationMode,
        topic: str | None,
    ) -> tuple[Conversation, list[LlmMessage]]:
        """Find the active conversation or start a new one.

        A conversation is closed by ``/new``, by switching roleplay scenes, or
        when it has been idle longer than ``idle_timeout``.
        """
        now = datetime.now(UTC)
        async with self._database.session() as session:
            conversation = await session.scalar(
                select(Conversation)
                .where(
                    Conversation.user_id == user.id,
                    Conversation.mode == mode,
                    Conversation.topic == topic,
                    Conversation.ended_at.is_(None),
                )
                .order_by(Conversation.started_at.desc())
                .limit(1)
            )

            if conversation is not None:
                last_activity = await session.scalar(
                    select(func.max(DbMessage.created_at)).where(
                        DbMessage.conversation_id == conversation.id
                    )
                )
                if (
                    last_activity is not None
                    and now - _as_aware(last_activity) > self._idle_timeout
                ):
                    logger.info(
                        "conversation_closed",
                        conversation_id=conversation.id,
                        reason="idle",
                    )
                    conversation.ended_at = now
                    conversation = None

            if conversation is None:
                conversation = Conversation(user_id=user.id, mode=mode, started_at=now)
                session.add(conversation)
                await session.flush()

            history = await self._history(session, conversation.id)

        return conversation, history

    async def _history(
        self,
        session: AsyncSession,
        conversation_id: int,
    ) -> list[LlmMessage]:
        """Load the most recent messages in chronological order.

        The query takes the newest ``history_window`` rows, then reverses them
        because the LLM expects oldest-first context.
        """
        rows = await session.scalars(
            select(DbMessage)
            .where(
                DbMessage.conversation_id == conversation_id,
                DbMessage.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
            )
            .order_by(DbMessage.id.desc())
            .limit(self._builder.history_window)
        )
        return [
            LlmMessage(role=row.role.value, content=row.content) for row in reversed(list(rows))
        ]

    async def _persist(
        self,
        *,
        user: User,
        conversation_id: int,
        user_text: str,
        payload: CorrectionPayload,
        response: LLMResponse,
        prompt_version: str,
        update_id: int | None,
    ) -> tuple[int, int]:
        """Store messages, corrections, new words and usage atomically."""
        user_row = DbMessage(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=user_text,
            update_id=update_id,
        )
        assistant_row = DbMessage(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=payload.reply,
        )

        async with self._database.session() as session:
            session.add_all((user_row, assistant_row))
            # Flush to obtain message ids used by corrections/vocabulary.
            await session.flush()
            session.add_all(
                DbCorrection(
                    message_id=user_row.id,
                    original=correction.original,
                    corrected=correction.corrected,
                    category=correction.category,
                    explanation=correction.explanation_ru,
                )
                for correction in payload.corrections
            )
            await VocabularyRepository(session).save_many(
                user.id,
                [(word.term, word.translation_ru, word.context) for word in payload.new_vocabulary],
                source_message_id=assistant_row.id,
            )
            session.add(
                self._usage.build_entry(
                    user_id=user.id,
                    conversation_id=conversation_id,
                    model=response.model,
                    prompt_version=prompt_version,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost_usd=response.cost_usd,
                    latency_ms=response.latency_ms,
                )
            )

        return user_row.id, assistant_row.id


def _as_aware(value: datetime) -> datetime:
    """SQLite can return naive datetimes; treat them as UTC before comparing."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
