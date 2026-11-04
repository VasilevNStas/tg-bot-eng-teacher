from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.db.base import Base
from app.db.database import Database
from app.db.enums import ContentKind
from app.db.models import AdminAudit, ContentItem, Topic, User
from app.services.admin.service import AdminService


async def make_database(tmp_path: Path) -> Database:
    database = Database()
    database.init(tmp_path / "bot.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def add_user(database: Database, tg_id: int = 100) -> None:
    async with database.session() as session:
        session.add(User(tg_id=tg_id, username=f"user{tg_id}"))


async def audit_count(database: Database) -> int:
    async with database.session() as session:
        value = await session.scalar(select(func.count()).select_from(AdminAudit))
    return int(value or 0)


async def test_block_and_unblock_are_audited(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await add_user(database)
    service = AdminService(database)

    assert await service.restriction(100) is None
    assert await service.block(100, reason="spam", actor="tg:1", source="admin_bot")

    restriction = await service.restriction(100)
    assert restriction is not None
    assert restriction.blocked is True
    assert restriction.muted is False
    assert restriction.reason == "spam"
    assert await audit_count(database) == 1

    assert await service.unblock(100, actor="tg:1", source="admin_bot")
    assert await service.restriction(100) is None
    assert await audit_count(database) == 2

    await database.dispose()


async def test_mute_expires_and_does_not_affect_block(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await add_user(database)
    service = AdminService(database)

    future = datetime.now(UTC) + timedelta(hours=1)
    assert await service.mute(100, until=future, reason="flood", actor="tg:1", source="admin_bot")

    restriction = await service.restriction(100)
    assert restriction is not None
    assert restriction.muted is True
    assert restriction.reason == "flood"

    expired = await service.restriction(100, now=future + timedelta(minutes=1))
    assert expired is None

    await database.dispose()


async def test_unknown_user_is_not_changed_but_audited(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    service = AdminService(database)

    assert not await service.block(999, reason="spam", actor="tg:1", source="cli")

    entries = await service.recent_audit()
    assert len(entries) == 1
    assert entries[0].success is False
    assert entries[0].details["error"] == "user_not_found"

    await database.dispose()


async def test_user_info_and_counts(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    await add_user(database)
    async with database.session() as session:
        topic = Topic(slug="t", title="T")
        session.add(topic)
        await session.flush()
        session.add(
            ContentItem(
                slug="i",
                topic_id=topic.id,
                kind=ContentKind.VOCABULARY,
                payload={"term": "word", "translation_ru": "слово"},
            )
        )
    service = AdminService(database)

    info = await service.get_user(100)
    assert info is not None
    assert info.tg_id == 100
    assert info.username == "user100"
    assert info.level == "A2"

    counts = await service.counts()
    assert counts.users_total == 1
    assert counts.users_new_7d == 1
    assert counts.content_items == 1

    users = await service.list_users()
    assert [user.tg_id for user in users] == [100]

    await database.dispose()
