import os

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import RedisStorage

from app.states import InspectionStates

TEST_REDIS_URL = os.getenv("TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(
    not TEST_REDIS_URL,
    reason="TEST_REDIS_URL is not configured for Redis integration tests",
)


@pytest.mark.asyncio
async def test_fsm_survives_redis_storage_recreation() -> None:
    assert TEST_REDIS_URL is not None
    key = StorageKey(bot_id=77, chat_id=88, user_id=99)
    first_storage = RedisStorage.from_url(TEST_REDIS_URL)
    first_context = FSMContext(storage=first_storage, key=key)
    await first_context.clear()
    await first_context.set_state(InspectionStates.comment)
    await first_context.update_data(order_number_normalized="REDIS123")
    await first_storage.close()

    restored_storage = RedisStorage.from_url(TEST_REDIS_URL)
    restored_context = FSMContext(storage=restored_storage, key=key)
    assert await restored_context.get_state() == InspectionStates.comment.state
    assert (await restored_context.get_data())["order_number_normalized"] == "REDIS123"
    await restored_context.clear()
    await restored_storage.close()
