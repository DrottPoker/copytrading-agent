import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services.paper_trading_service import PaperCopyBatchResult
from app.services.realtime_execution_inbox_service import ClaimedRealtimeExecution
from app.services.realtime_fill_service import StoredRealtimeFills
from app.workers import monitor_worker


class DummySession:
    async def __aenter__(self) -> "DummySession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def dummy_sessionmaker() -> DummySession:
    return DummySession()


@pytest.mark.asyncio
async def test_handle_websocket_message_prioritizes_live_copy(monkeypatch) -> None:
    calls: list[str] = []
    published_events: list[str] = []
    stored_fill = {
        "coin": "HYPE",
        "externalFillId": "fill-1",
        "price": "60",
        "side": "buy",
    }

    async def fake_store_realtime_fills(*_args: object, **_kwargs: object) -> StoredRealtimeFills:
        return StoredRealtimeFills(
            wallet_address="0xsource",
            fetched=1,
            inserted=1,
            duplicate=0,
            is_snapshot=False,
            latest_fill_time_ms=1,
            inserted_rows=[stored_fill],
        )

    async def fake_process_live_copy_fills(
        *_args: object,
        **_kwargs: object,
    ) -> PaperCopyBatchResult:
        calls.append("live")
        return PaperCopyBatchResult(processed_fills=1)

    async def fake_process_paper_copy_fills(
        *_args: object,
        **_kwargs: object,
    ) -> PaperCopyBatchResult:
        calls.append("paper")
        return PaperCopyBatchResult(processed_fills=1)

    async def fake_publish_event(
        *_args: object,
        event_type: str,
        **_kwargs: object,
    ) -> None:
        published_events.append(event_type)

    monkeypatch.setattr(monitor_worker, "store_realtime_fills", fake_store_realtime_fills)
    monkeypatch.setattr(monitor_worker, "process_live_copy_fills", fake_process_live_copy_fills)
    monkeypatch.setattr(monitor_worker, "process_paper_copy_fills", fake_process_paper_copy_fills)
    monkeypatch.setattr(monitor_worker, "publish_event", fake_publish_event)

    settings = SimpleNamespace(
        live_trading_enabled=True,
        paper_trading_enabled=True,
        paper_copy_enabled=True,
    )
    message: dict[str, Any] = {
        "channel": "userFills",
        "data": {
            "user": "0xsource",
            "fills": [stored_fill],
            "isSnapshot": False,
        },
    }

    await monitor_worker.handle_websocket_message(
        message,
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        wallet_addresses=["0xsource"],
        settings=settings,
    )

    assert calls == ["live", "paper"]
    assert published_events == ["fill", "live_copy", "paper_copy"]


@pytest.mark.asyncio
async def test_execution_finishes_before_blocked_presentation_events(monkeypatch) -> None:
    sequence: list[str] = []
    execution_complete = asyncio.Event()
    publication_started = asyncio.Event()
    release_publication = asyncio.Event()
    stored = StoredRealtimeFills(
        wallet_address="0xsource",
        fetched=1,
        inserted=1,
        duplicate=0,
        is_snapshot=False,
        latest_fill_time_ms=1,
        inserted_rows=[
            {
                "coin": "HYPE",
                "externalFillId": "fill-1",
                "price": "60",
                "side": "buy",
            }
        ],
    )

    async def fake_live(*_args: object, **_kwargs: object) -> PaperCopyBatchResult:
        sequence.append("live")
        return PaperCopyBatchResult(processed_fills=1)

    async def fake_paper(*_args: object, **_kwargs: object) -> PaperCopyBatchResult:
        sequence.append("paper")
        execution_complete.set()
        return PaperCopyBatchResult(processed_fills=1)

    async def blocking_publish(
        *_args: object,
        event_type: str,
        **_kwargs: object,
    ) -> None:
        sequence.append(f"event:{event_type}")
        publication_started.set()
        await release_publication.wait()

    monkeypatch.setattr(monitor_worker, "process_live_copy_fills", fake_live)
    monkeypatch.setattr(monitor_worker, "process_paper_copy_fills", fake_paper)
    monkeypatch.setattr(monitor_worker, "publish_event", blocking_publish)
    task = asyncio.create_task(
        monitor_worker.process_stored_realtime_fills(
            stored,
            sessionmaker=dummy_sessionmaker,
            redis=object(),
            settings=SimpleNamespace(
                live_trading_enabled=True,
                paper_trading_enabled=True,
                paper_copy_enabled=True,
            ),
        )
    )

    await asyncio.wait_for(execution_complete.wait(), timeout=1)
    await asyncio.wait_for(publication_started.wait(), timeout=1)

    assert sequence[:2] == ["live", "paper"]
    assert all(item.startswith("event:") for item in sequence[2:])
    assert task.done() is False

    release_publication.set()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_presentation_event_batch_has_bounded_overall_timeout(monkeypatch) -> None:
    started = 0

    async def blocked_publish(*_args: object, **_kwargs: object) -> None:
        nonlocal started
        started += 1
        await asyncio.Event().wait()

    monkeypatch.setattr(monitor_worker, "publish_event", blocked_publish)
    monkeypatch.setattr(monitor_worker, "RUNTIME_EVENT_BATCH_TIMEOUT_SECONDS", 0.01)
    events = [
        {
            "event_type": "fill",
            "channel": "events:fills",
            "message": f"Fill {index}",
            "payload": {},
        }
        for index in range(100)
    ]

    await asyncio.wait_for(
        monitor_worker.publish_event_batch(object(), events),
        timeout=1,
    )

    assert 0 < started <= monitor_worker.RUNTIME_EVENT_BATCH_CONCURRENCY


@pytest.mark.asyncio
async def test_handle_websocket_snapshot_prioritizes_live_recovery(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_store_realtime_fills(*_args: object, **_kwargs: object) -> StoredRealtimeFills:
        return StoredRealtimeFills(
            wallet_address="0xsource",
            fetched=1,
            inserted=0,
            duplicate=1,
            is_snapshot=True,
            latest_fill_time_ms=1,
            inserted_rows=[],
        )

    async def fake_live_recovery_once(*_args: object, **_kwargs: object) -> PaperCopyBatchResult:
        calls.append("live")
        return PaperCopyBatchResult()

    async def fake_paper_recovery_once(*_args: object, **_kwargs: object) -> PaperCopyBatchResult:
        calls.append("paper")
        return PaperCopyBatchResult()

    async def fake_publish_event(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(monitor_worker, "store_realtime_fills", fake_store_realtime_fills)
    monkeypatch.setattr(monitor_worker, "run_live_copy_recovery_once", fake_live_recovery_once)
    monkeypatch.setattr(monitor_worker, "run_paper_copy_recovery_once", fake_paper_recovery_once)
    monkeypatch.setattr(monitor_worker, "publish_event", fake_publish_event)

    settings = SimpleNamespace(
        live_trading_enabled=True,
        paper_trading_enabled=True,
        paper_copy_enabled=True,
    )
    message: dict[str, Any] = {
        "channel": "userFills",
        "data": {
            "user": "0xsource",
            "fills": [{"coin": "HYPE", "externalFillId": "fill-1"}],
            "isSnapshot": True,
        },
    }

    await monitor_worker.handle_websocket_message(
        message,
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        wallet_addresses=["0xsource"],
        settings=settings,
    )

    assert calls == ["live", "paper"]


@pytest.mark.asyncio
async def test_startup_copy_recovery_prioritizes_live_recovery(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_live_recovery_once(*_args: object, **_kwargs: object) -> PaperCopyBatchResult:
        calls.append("live")
        return PaperCopyBatchResult()

    async def fake_paper_recovery_once(*_args: object, **_kwargs: object) -> PaperCopyBatchResult:
        calls.append("paper")
        return PaperCopyBatchResult()

    monkeypatch.setattr(monitor_worker, "run_live_copy_recovery_once", fake_live_recovery_once)
    monkeypatch.setattr(monitor_worker, "run_paper_copy_recovery_once", fake_paper_recovery_once)

    settings = SimpleNamespace(
        live_trading_enabled=True,
        paper_trading_enabled=True,
        paper_copy_enabled=True,
    )

    await monitor_worker.run_startup_copy_recovery_once(
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        settings=settings,
    )

    assert calls == ["live", "paper"]


@pytest.mark.asyncio
async def test_runtime_event_failure_is_best_effort(monkeypatch) -> None:
    async def failing_publish(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(monitor_worker, "publish_realtime_event", failing_publish)

    result = await monitor_worker.publish_event(
        object(),
        event_type="fill",
        channel="events:fills",
        message="Fill persisted.",
        payload={"walletAddress": "0xsource"},
    )

    assert result is None


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("all", ("trading", "maintenance")),
        ("trading", ("trading",)),
        ("maintenance", ("maintenance",)),
    ],
)
def test_worker_role_capability_mapping(role: str, expected: tuple[str, ...]) -> None:
    assert monitor_worker.worker_capabilities(SimpleNamespace(worker_role=role)) == expected


@pytest.mark.asyncio
async def test_scoring_failure_prevents_prune_after_pool_import(monkeypatch) -> None:
    calls: list[str] = []
    stop_event = asyncio.Event()

    async def fake_import(*_args: object, **_kwargs: object) -> SimpleNamespace:
        calls.append("import")
        return SimpleNamespace(
            scanned=1,
            imported_wallets=1,
            fetched=1,
            inserted=1,
            duplicate=0,
            failed=0,
            limit=10,
        )

    async def fake_scoring(*_args: object, **_kwargs: object) -> bool:
        calls.append("scoring")
        return False

    async def fake_prune(*_args: object, **_kwargs: object) -> None:
        calls.append("prune")

    async def fake_publish(*_args: object, **_kwargs: object) -> None:
        return None

    async def stop_after_iteration(event: asyncio.Event, _seconds: int) -> None:
        event.set()

    monkeypatch.setattr(monitor_worker, "import_due_pool_wallet_fills", fake_import)
    monkeypatch.setattr(monitor_worker, "run_wallet_scoring_once", fake_scoring)
    monkeypatch.setattr(monitor_worker, "run_wallet_prune_once", fake_prune)
    monkeypatch.setattr(monitor_worker, "publish_event", fake_publish)
    monkeypatch.setattr(monitor_worker, "sleep_until_stop", stop_after_iteration)

    settings = SimpleNamespace(
        worker_role="maintenance",
        pool_fill_import_run_on_worker_start=True,
        pool_fill_import_start_delay_seconds=0,
        pool_fill_import_interval_seconds=600,
        pool_fill_import_batch_size=10,
        pool_fill_import_days=30,
        pool_fill_import_max_pages=5,
        pool_fill_import_min_wallet_interval_seconds=600,
        pool_fill_import_overlap_seconds=300,
        pool_fill_import_max_batches=1,
        paper_trading_enabled=True,
        paper_copy_enabled=True,
        scoring_enabled=True,
        wallet_prune_after_pool_import_enabled=True,
    )

    await monitor_worker.run_pool_fill_import_loop(
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        stop_event=stop_event,
        settings=settings,
    )

    assert calls == ["import", "scoring"]


@pytest.mark.asyncio
async def test_realtime_queue_overflow_preserves_durable_inbox_work(monkeypatch) -> None:
    persisted: list[StoredRealtimeFills] = []
    published_events: list[str] = []
    existing = StoredRealtimeFills(
        wallet_address="0xexisting",
        fetched=1,
        inserted=1,
        duplicate=0,
        is_snapshot=False,
        latest_fill_time_ms=1,
        inserted_rows=[{"externalFillId": "existing-fill"}],
        inbox_id="existing-inbox",
    )
    stored = StoredRealtimeFills(
        wallet_address="0xsource",
        fetched=1,
        inserted=1,
        duplicate=0,
        is_snapshot=False,
        latest_fill_time_ms=2,
        inserted_rows=[{"externalFillId": "durable-fill"}],
        inbox_id="durable-inbox",
    )
    execution_queue: asyncio.Queue[monitor_worker.RealtimeExecutionWorkItem] = asyncio.Queue(
        maxsize=1
    )
    execution_queue.put_nowait(
        monitor_worker.RealtimeExecutionWorkItem(inbox_id=existing.inbox_id or "")
    )
    runtime = monitor_worker.WorkerRuntimeState(
        role="trading",
        capabilities=("trading",),
        realtime_queue_capacity=1,
    )

    async def fake_store(*_args: object, **_kwargs: object) -> StoredRealtimeFills:
        persisted.append(stored)
        return stored

    async def fake_publish(
        *_args: object,
        event_type: str,
        **_kwargs: object,
    ) -> None:
        published_events.append(event_type)

    monkeypatch.setattr(monitor_worker, "store_realtime_fills", fake_store)
    monkeypatch.setattr(monitor_worker, "publish_event", fake_publish)

    await monitor_worker.handle_websocket_message(
        {
            "channel": "userFills",
            "data": {
                "user": "0xsource",
                "fills": [{"coin": "HYPE", "externalFillId": "durable-fill"}],
                "isSnapshot": False,
            },
        },
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        wallet_addresses=["0xsource"],
        settings=SimpleNamespace(),
        execution_queue=execution_queue,
        runtime=runtime,
    )

    queued = execution_queue.get_nowait()
    execution_queue.task_done()
    assert persisted == [stored]
    assert queued.inbox_id == "existing-inbox"
    assert published_events == ["realtime_execution_queue_full"]
    assert runtime.realtime_queue_depth == 1
    assert runtime.realtime_queue_capacity == 1
    assert runtime.realtime_queue_dropped == 1


@pytest.mark.asyncio
async def test_realtime_execution_loop_replays_durable_first_fill_without_wakeup(
    monkeypatch,
) -> None:
    inbox_id = uuid4()
    stored = StoredRealtimeFills(
        wallet_address="0xsource",
        fetched=1,
        inserted=1,
        duplicate=0,
        is_snapshot=False,
        latest_fill_time_ms=2,
        inserted_rows=[{"externalFillId": "first-fill"}],
        inbox_id=str(inbox_id),
    )
    claims = [
        ClaimedRealtimeExecution(
            inbox_id=inbox_id,
            stored=stored,
            attempt_count=1,
        ),
        None,
    ]
    processed: list[str] = []
    completed: list[object] = []

    async def fake_claim(*_args: object, **_kwargs: object):
        return claims.pop(0)

    async def fake_process(item: StoredRealtimeFills, **_kwargs: object) -> None:
        processed.extend(str(row["externalFillId"]) for row in item.inserted_rows)

    async def fake_complete(*_args: object, inbox_id: object, **_kwargs: object) -> bool:
        completed.append(inbox_id)
        return True

    monkeypatch.setattr(monitor_worker, "claim_next_realtime_execution", fake_claim)
    monkeypatch.setattr(monitor_worker, "process_stored_realtime_fills", fake_process)
    monkeypatch.setattr(monitor_worker, "complete_realtime_execution", fake_complete)
    stop_event = asyncio.Event()
    stop_event.set()
    intake_closed_event = asyncio.Event()
    intake_closed_event.set()
    runtime = monitor_worker.WorkerRuntimeState(
        role="trading",
        capabilities=("trading",),
    )

    await monitor_worker.run_realtime_execution_loop(
        execution_queue=asyncio.Queue(maxsize=1),
        sessionmaker=object(),
        redis=object(),
        stop_event=stop_event,
        intake_closed_event=intake_closed_event,
        settings=SimpleNamespace(),
        price_cache=None,
        runtime=runtime,
    )

    assert processed == ["first-fill"]
    assert completed == [inbox_id]


@pytest.mark.asyncio
async def test_realtime_execution_waits_for_intake_to_close_before_shutdown(
    monkeypatch,
) -> None:
    async def fake_claim(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(monitor_worker, "claim_next_realtime_execution", fake_claim)
    stop_event = asyncio.Event()
    stop_event.set()
    intake_closed_event = asyncio.Event()
    execution_queue: asyncio.Queue[monitor_worker.RealtimeExecutionWorkItem] = asyncio.Queue(
        maxsize=1
    )
    runtime = monitor_worker.WorkerRuntimeState(
        role="trading",
        capabilities=("trading",),
    )
    task = asyncio.create_task(
        monitor_worker.run_realtime_execution_loop(
            execution_queue=execution_queue,
            sessionmaker=object(),
            redis=object(),
            stop_event=stop_event,
            intake_closed_event=intake_closed_event,
            settings=SimpleNamespace(),
            price_cache=None,
            runtime=runtime,
        )
    )
    await asyncio.sleep(0)

    assert task.done() is False

    intake_closed_event.set()
    execution_queue.put_nowait(monitor_worker.RealtimeExecutionWorkItem(inbox_id="wake"))
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_trading_worker_registers_core_loops_with_supervisor(monkeypatch) -> None:
    supervised: list[str] = []
    stop_event = asyncio.Event()

    async def fake_publish(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_supervised(*, name: str, **_kwargs: object) -> None:
        supervised.append(name)
        stop_event.set()

    monkeypatch.setattr(monitor_worker, "publish_event", fake_publish)
    monkeypatch.setattr(monitor_worker, "run_supervised_worker_loop", fake_supervised)

    settings = SimpleNamespace(
        worker_role="trading",
        hyperliquid_network="mainnet",
        max_realtime_wallets=10,
        paper_trading_enabled=False,
        paper_copy_enabled=False,
        live_trading_enabled=False,
        live_trading_reduce_only_when_stopped=False,
        worker_shutdown_drain_seconds=1,
        realtime_execution_queue_size=2,
    )
    runtime = monitor_worker.WorkerRuntimeState(
        role="trading",
        capabilities=("trading",),
        realtime_queue_capacity=2,
    )

    await monitor_worker.run_owned_monitor_services(
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        stop_event=stop_event,
        settings=settings,
        runtime=runtime,
    )

    assert supervised == ["heartbeat", "realtime-execution", "realtime-subscription"]
