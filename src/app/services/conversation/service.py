"""One conversational turn from input to persisted response."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import Database
from app.db.enums import ConversationMode, MessageRole
from app.db.models import Conversation, User
from app.db.models import Correction as DbCorrection
from app.db.models import Message as DbMessage
from app.db.repositories.vocabulary import VocabularyRepository
from app.llm.errors import LLMInvalidResponse
from app.llm.port import LLMClient, LLMResponse
from app.llm.port import Message as LlmMessage
from app.services.correction.schema import (
    CorrectionItem,
    CorrectionPayload,
    NewVocabularyItem,
)
from app.services.prompts.builder import PromptBuilder, PromptContext
from app.services.prompts.registry import PromptTemplate
from app.services.usage.service import UsageService

STRUCTURED_MODES = frozenset({ConversationMode.CHAT, ConversationMode.CORRECTION})


@dataclass(frozen=True, slots=True)
class ConversationTurn:
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


class ConversationService:
    def __init__(
        self,
        database: Database,
        llm: LLMClient,
        usage: UsageService,
        builder: PromptBuilder,
        *,
        model_fast: str,
        model_smart: str,
        max_output_tokens: int,
        idle_timeout: timedelta = timedelta(minutes=30),
    ) -> None:
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        self._database = database
        self._llm = llm
        self._usage = usage
        self._builder = builder
        self._model_fast = model_fast
        self._model_smart = model_smart
        self._max_output_tokens = max_output_tokens
        self._idle_timeout = idle_timeout

    async def respond(
        self,
        user: User,
        text: str,
        *,
        mode: ConversationMode = ConversationMode.CHAT,
        update_id: int | None = None,
        scenario: PromptTemplate | None = None,
    ) -> ConversationTurn:
        topic = scenario.id if scenario is not None else None
        conversation, history = await self._prepare(user, mode, topic)

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

        await self._usage.ensure_available(
            user.id,
            estimated_tokens=prompt.estimated_input_tokens + self._max_output_tokens,
        )

        structured = mode in STRUCTURED_MODES
        response = await self._llm.complete(
            prompt.messages,
            model=self._model_for(mode),
            temperature=0.4 if mode is ConversationMode.CORRECTION else 0.7,
            max_tokens=self._max_output_tokens,
            response_schema=CorrectionPayload if structured else None,
        )
        payload = self._payload(response, structured)

        user_message_id, assistant_message_id = await self._persist(
            user=user,
            conversation_id=conversation.id,
            user_text=text,
            payload=payload,
            response=response,
            prompt_version=prompt.prompt_version,
            update_id=update_id,
        )

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
        )

    async def start_roleplay(self, user_id: int, scenario_id: str) -> None:
        now = datetime.now(UTC)
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
            if same:
                return
            session.add(
                Conversation(
                    user_id=user_id,
                    mode=ConversationMode.ROLEPLAY,
                    topic=scenario_id,
                    started_at=now,
                )
            )

    async def stop_roleplay(self, user_id: int) -> str | None:
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
            return conversation.topic

    async def active_roleplay(self, user_id: int) -> str | None:
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

    def _model_for(self, mode: ConversationMode) -> str:
        if mode is ConversationMode.CORRECTION:
            return self._model_smart
        return self._model_fast

    @staticmethod
    def _payload(response: LLMResponse, structured: bool) -> CorrectionPayload:
        if not structured:
            return CorrectionPayload(
                reply=response.content,
                corrections=[],
                new_vocabulary=[],
            )
        if response.parsed is None:
            raise LLMInvalidResponse("structured mode returned no parsed payload")
        return CorrectionPayload.model_validate(response.parsed)

    async def _prepare(
        self,
        user: User,
        mode: ConversationMode,
        topic: str | None,
    ) -> tuple[Conversation, list[LlmMessage]]:
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
            model=response.model,
            prompt_version=prompt_version,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

        async with self._database.session() as session:
            session.add_all((user_row, assistant_row))
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
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
