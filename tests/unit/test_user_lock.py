from __future__ import annotations

import asyncio

from app.bot.middlewares.user_lock import UserLockRegistry


async def test_same_user_is_serialized() -> None:
    registry = UserLockRegistry()
    inside = 0
    max_inside = 0

    async def worker() -> None:
        nonlocal inside, max_inside
        async with registry.acquire(1):
            inside += 1
            max_inside = max(max_inside, inside)
            await asyncio.sleep(0.01)
            inside -= 1

    await asyncio.gather(worker(), worker(), worker())

    assert max_inside == 1


async def test_different_users_run_in_parallel() -> None:
    registry = UserLockRegistry()
    inside = 0
    max_inside = 0

    async def worker(user_id: int) -> None:
        nonlocal inside, max_inside
        async with registry.acquire(user_id):
            inside += 1
            max_inside = max(max_inside, inside)
            await asyncio.sleep(0.01)
            inside -= 1

    await asyncio.gather(worker(1), worker(2))

    assert max_inside == 2


async def test_lock_is_released_after_use() -> None:
    registry = UserLockRegistry()

    async with registry.acquire(42):
        assert registry.active_locks == 1

    assert registry.active_locks == 0
