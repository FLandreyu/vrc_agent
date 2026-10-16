"""聊天框队列：限长、节流、过期丢弃、暂停清空。"""

from __future__ import annotations

import pytest

from vrc_agent.clock import FakeClock
from vrc_agent.dialogue import ChatItem, ChatQueue, DialogueError

MS = 1_000_000


def make_queue(**changes) -> tuple[ChatQueue, FakeClock]:
    clock = FakeClock(1_000_000_000)
    options = {
        "session_id": "test",
        "clock": clock,
        "max_pending": 3,
        "ttl_ms": 5_000,
        "min_interval_ms": 1_000,
    }
    options.update(changes)
    return ChatQueue(**options), clock


def test_enqueue_then_take_returns_the_same_text() -> None:
    queue, _ = make_queue()
    item = queue.enqueue("hello world")
    taken = queue.take()
    assert taken is not None
    assert taken.item_id == item.item_id
    assert taken.text == "hello world"
    assert queue.stats()["sent"] == 1
    assert queue.take() is None  # 已经空了


def test_the_queue_is_bounded_and_drops_the_oldest() -> None:
    queue, _ = make_queue(max_pending=2, min_interval_ms=0)
    queue.enqueue("first")
    queue.enqueue("second")
    queue.enqueue("third")
    stats = queue.stats()
    assert stats["dropped_full"] == 1
    assert queue.take().text == "second"  # 最旧的被淘汰，最新的一定保留
    assert queue.take().text == "third"


def test_throttle_returns_none_without_consuming_the_item() -> None:
    queue, clock = make_queue(min_interval_ms=1_000)
    queue.enqueue("one")
    assert queue.take() is not None
    queue.enqueue("two")
    assert queue.take() is None  # 距上次不足 1 秒：既不发送也不丢弃
    assert queue.stats()["throttled"] == 1
    assert queue.stats()["pending"] == 1
    clock.advance_ms(1_000)
    assert queue.take().text == "two"


def test_next_due_ns_tells_the_caller_when_to_come_back() -> None:
    queue, clock = make_queue(min_interval_ms=1_000)
    assert queue.next_due_ns() is None  # 没有待发内容
    queue.enqueue("one")
    assert queue.next_due_ns() == clock.now_ns()
    queue.take()
    queue.enqueue("two")
    assert queue.next_due_ns() == clock.now_ns() + 1_000 * MS
    clock.advance_ms(1_500)
    assert queue.next_due_ns() == clock.now_ns()  # 已经过了窗口


def test_expired_items_are_dropped_and_never_sent() -> None:
    queue, clock = make_queue(ttl_ms=500, min_interval_ms=0)
    queue.enqueue("stale")
    clock.advance_ms(499)
    assert queue.peek() is not None
    clock.advance_ms(1)
    assert queue.take() is None  # 过期即弃，不补播
    assert queue.stats()["dropped_expired"] == 1
    assert queue.stats()["sent"] == 0


def test_take_skips_an_expired_head_and_returns_the_next_live_item() -> None:
    queue, clock = make_queue(ttl_ms=500, min_interval_ms=0)
    queue.enqueue("old")
    clock.advance_ms(300)
    queue.enqueue("fresh")
    clock.advance_ms(250)  # old 过期，fresh 还活着
    item = queue.take()
    assert item is not None and item.text == "fresh"
    assert queue.stats()["dropped_expired"] == 1


def test_pause_clears_pending_and_refuses_new_speech() -> None:
    queue, _ = make_queue(min_interval_ms=0)
    for index in range(3):
        queue.enqueue(f"item {index}")
    assert queue.pause("OPERATOR_PAUSE") == 3
    assert queue.stats()["pending"] == 0
    assert queue.take() is None
    with pytest.raises(DialogueError) as info:
        queue.enqueue("while paused")
    assert info.value.failure == "PAUSED"
    assert queue.stats()["rejected_paused"] == 1
    queue.resume()
    queue.enqueue("after resume")
    assert queue.take().text == "after resume"


def test_text_must_satisfy_the_chatbox_contract() -> None:
    queue, _ = make_queue()
    for bad in ("", "x" * 200, "line\n" * 10, "nul\x00byte"):
        with pytest.raises(DialogueError) as info:
            queue.enqueue(bad)
        assert info.value.failure == "TEXT_INVALID"
    assert queue.stats()["enqueued"] == 0


def test_stats_never_contain_text() -> None:
    queue, _ = make_queue()
    queue.enqueue("secret words from a real person")
    serialised = repr(queue.stats())
    assert "secret" not in serialised
    assert "words" not in serialised


def test_items_expose_no_clock_dependent_fields() -> None:
    # 队列项不带「年龄」这类依赖当前时间的字段：读取它的时间点不同会得到不同答案，
    # 这种字段一旦出现就会有人拿它做判断。年龄由调用方用 now_ns 自己算。
    assert "age_ms" not in ChatItem.model_fields
    item = make_queue()[0].enqueue("hello")
    assert set(ChatItem.model_fields) == {
        "item_id",
        "text",
        "source",
        "created_at_ns",
        "expires_at_ns",
    }
    assert item.expires_at_ns > item.created_at_ns


def test_bounds_are_enforced_at_construction() -> None:
    with pytest.raises(ValueError):
        ChatQueue(max_pending=0)
    with pytest.raises(ValueError):
        ChatQueue(ttl_ms=100)
    with pytest.raises(ValueError):
        ChatQueue(min_interval_ms=120_000)
