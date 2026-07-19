import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services.live_copy_state_service import LIVE_COPY_ORIGIN_REALTIME
from app.services.live_copy_work_service import ClaimedLiveCopyWork
from app.services.live_trading_service import LiveReconciliationError
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
async def test_handle_websocket_message_never_executes_live_copy_inline(monkeypatch) -> None:
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
        raise AssertionError("live execution must be consumed from durable live-copy work")

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

    assert calls == ["paper"]
    assert published_events == ["fill", "paper_copy"]


@pytest.mark.asyncio
async def test_paper_execution_finishes_before_blocked_presentation_events(monkeypatch) -> None:
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
        raise AssertionError("legacy inbox processing must not execute live copy")

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

    assert sequence[0] == "paper"
    assert all(item.startswith("event:") for item in sequence[1:])
    assert task.done() is False

    release_publication.set()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_duplicate_realtime_fill_reaches_paper_but_not_inline_live_execution(
    monkeypatch,
) -> None:
    copied_rows: list[tuple[str, list[dict[str, Any]]]] = []
    published_events: list[str] = []
    execution_row = {
        "coin": "HYPE",
        "externalFillId": "poll-inserted-fill",
        "price": "60",
        "side": "buy",
        "size": "1",
        "timestampMs": 1,
        "rawJson": {"dir": "Open Long"},
    }
    stored = StoredRealtimeFills(
        wallet_address="0xsource",
        fetched=1,
        inserted=0,
        duplicate=1,
        is_snapshot=False,
        latest_fill_time_ms=1,
        inserted_rows=[],
        execution_rows=[execution_row],
    )

    async def fake_live(
        *_args: object,
        fills: list[dict[str, Any]],
        **_kwargs: object,
    ) -> PaperCopyBatchResult:
        raise AssertionError("live execution must use the live-copy work queue")

    async def fake_paper(
        *_args: object,
        fills: list[dict[str, Any]],
        **_kwargs: object,
    ) -> PaperCopyBatchResult:
        copied_rows.append(("paper", fills))
        return PaperCopyBatchResult(processed_fills=1)

    async def fake_publish(
        *_args: object,
        event_type: str,
        **_kwargs: object,
    ) -> None:
        published_events.append(event_type)

    monkeypatch.setattr(monitor_worker, "process_live_copy_fills", fake_live)
    monkeypatch.setattr(monitor_worker, "process_paper_copy_fills", fake_paper)
    monkeypatch.setattr(monitor_worker, "publish_event", fake_publish)

    await monitor_worker.process_stored_realtime_fills(
        stored,
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        settings=SimpleNamespace(
            live_trading_enabled=True,
            paper_trading_enabled=True,
            paper_copy_enabled=True,
        ),
    )

    assert copied_rows == [("paper", [execution_row])]
    assert published_events == ["paper_copy"]


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
    calls: list[tuple[str, bool | None]] = []

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

    async def fake_live_recovery_once(*_args: object, **kwargs: object) -> PaperCopyBatchResult:
        calls.append(("live", kwargs.get("log_lock_contention")))
        return PaperCopyBatchResult()

    async def fake_paper_recovery_once(*_args: object, **kwargs: object) -> PaperCopyBatchResult:
        calls.append(("paper", kwargs.get("log_lock_contention")))
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

    assert calls == [("live", False), ("paper", False)]


@pytest.mark.asyncio
async def test_monitor_services_waits_for_existing_capability_lease(monkeypatch, caplog) -> None:
    attempts = 0
    sleeps: list[int] = []

    async def fake_run_monitor_services(**_kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise monitor_worker.WorkerCapabilityLeaseUnavailableError(
                "Worker capability is already owned: worker_runtime:trading."
            )

    async def fake_sleep(_stop_event: asyncio.Event, seconds: int) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(monitor_worker, "run_monitor_services", fake_run_monitor_services)
    monkeypatch.setattr(monitor_worker, "sleep_until_stop", fake_sleep)
    caplog.set_level("INFO")

    await monitor_worker.run_monitor_services_with_lease_retry(
        sessionmaker=object(),
        redis=object(),
        stop_event=asyncio.Event(),
        settings=SimpleNamespace(worker_loop_restart_delay_seconds=5),
    )

    assert attempts == 2
    assert sleeps == [5]
    assert "worker capability lease is still owned" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_busy_scheduled_live_reconciliation_is_deferred(monkeypatch, caplog) -> None:
    published_payloads: list[dict[str, object]] = []

    class ScalarResult:
        def all(self) -> list[str]:
            return ["live-account"]

    class ReconciliationSession(DummySession):
        async def scalars(self, _statement: object) -> ScalarResult:
            return ScalarResult()

        async def scalar(self, _statement: object) -> SimpleNamespace:
            return SimpleNamespace(key="live-account", account_type="live")

        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    @asynccontextmanager
    async def fake_job_lock(*_args: object, **_kwargs: object):
        yield

    async def fake_dispatch_recovery(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(inspected=0)

    async def fake_resume_close_all(*_args: object, **_kwargs: object) -> list[object]:
        return []

    async def busy_reconciliation(*_args: object, **_kwargs: object) -> None:
        raise LiveReconciliationError(
            "Live execution or reconciliation is already running for this account.",
            status_code=409,
        )

    async def capture_event(*_args: object, payload: dict[str, object], **_kwargs: object) -> None:
        published_payloads.append(payload)

    monkeypatch.setattr(monitor_worker, "job_lock", fake_job_lock)
    monkeypatch.setattr(monitor_worker, "recover_live_order_dispatches", fake_dispatch_recovery)
    monkeypatch.setattr(monitor_worker, "resume_live_close_all_operations", fake_resume_close_all)
    monkeypatch.setattr(monitor_worker, "reconcile_live_trading_account", busy_reconciliation)
    monkeypatch.setattr(monitor_worker, "publish_event", capture_event)
    caplog.set_level("INFO")

    results = await monitor_worker.run_live_trading_reconciliation_once(
        sessionmaker=ReconciliationSession,
        redis=object(),
        settings=SimpleNamespace(live_trading_reconciliation_interval_seconds=30),
    )

    assert results == []
    assert published_payloads[-1]["deferredAccounts"] == ["live-account"]
    assert published_payloads[-1]["failedAccounts"] == []
    assert "reconciliation deferred because account execution is busy" in caplog.text
    assert "Traceback" not in caplog.text


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
async def test_realtime_execution_loop_replays_durable_duplicate_fill_without_wakeup(
    monkeypatch,
) -> None:
    inbox_id = uuid4()
    stored = StoredRealtimeFills(
        wallet_address="0xsource",
        fetched=1,
        inserted=0,
        duplicate=1,
        is_snapshot=False,
        latest_fill_time_ms=2,
        inserted_rows=[],
        execution_rows=[{"externalFillId": "duplicate-fill"}],
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
        processed.extend(str(row["externalFillId"]) for row in item.rows_for_execution)

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

    assert processed == ["duplicate-fill"]
    assert completed == [inbox_id]


@pytest.mark.asyncio
async def test_live_copy_work_loop_claims_processes_and_completes_durable_work(
    monkeypatch,
) -> None:
    work_id = uuid4()
    wallet_fill_id = uuid4()
    claimed_at = datetime(2026, 7, 19, 10, tzinfo=UTC)
    claimed = ClaimedLiveCopyWork(
        id=work_id,
        wallet_fill_id=wallet_fill_id,
        wallet_address="0xsource",
        source_fill_id="source-fill-1",
        origin=LIVE_COPY_ORIGIN_REALTIME,
        claimed_at=claimed_at,
        attempt_count=1,
    )
    claims = [claimed, None]
    processed: list[dict[str, object]] = []
    completed: list[dict[str, object]] = []

    async def fake_claim(*_args: object, **_kwargs: object):
        return claims.pop(0)

    async def fake_load(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(received_at=claimed_at)

    async def fake_process(*_args: object, **kwargs: object) -> PaperCopyBatchResult:
        processed.append(kwargs)
        return PaperCopyBatchResult(processed_fills=1)

    async def fake_complete(*_args: object, **kwargs: object) -> bool:
        completed.append(kwargs)
        return True

    async def fake_publish(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(monitor_worker, "claim_next_live_copy_work", fake_claim)
    monkeypatch.setattr(monitor_worker, "load_claimed_live_copy_wallet_fill", fake_load)
    monkeypatch.setattr(
        monitor_worker,
        "paper_source_fill_from_wallet_fill",
        lambda _fill: {"id": "1"},
    )
    monkeypatch.setattr(monitor_worker, "process_live_copy_fills", fake_process)
    monkeypatch.setattr(monitor_worker, "complete_live_copy_work", fake_complete)
    monkeypatch.setattr(monitor_worker, "publish_event", fake_publish)
    stop_event = asyncio.Event()
    stop_event.set()
    intake_closed_event = asyncio.Event()
    intake_closed_event.set()
    runtime = monitor_worker.WorkerRuntimeState(role="trading", capabilities=("trading",))

    await monitor_worker.run_live_copy_work_loop(
        work_queue=asyncio.Queue(maxsize=1),
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        stop_event=stop_event,
        intake_closed_event=intake_closed_event,
        settings=SimpleNamespace(
            realtime_execution_claim_timeout_seconds=300,
            realtime_execution_retry_base_seconds=5,
        ),
        price_cache=None,
        runtime=runtime,
    )

    assert len(processed) == 1
    assert processed[0]["source_wallet"] == "0xsource"
    assert processed[0]["fills"] == [{"id": "1"}]
    assert processed[0]["origin"] == LIVE_COPY_ORIGIN_REALTIME
    assert processed[0]["realtime_observed_at"] == claimed_at
    assert processed[0]["execution_claimed_at"] == claimed_at
    assert completed == [{"work_id": work_id, "owner": f"{runtime.instance_id}:live-copy-work"}]


@pytest.mark.asyncio
async def test_live_copy_work_loop_retries_after_processing_failure(monkeypatch) -> None:
    work_id = uuid4()
    claimed = ClaimedLiveCopyWork(
        id=work_id,
        wallet_fill_id=uuid4(),
        wallet_address="0xsource",
        source_fill_id="source-fill-1",
        origin=LIVE_COPY_ORIGIN_REALTIME,
        claimed_at=datetime(2026, 7, 19, 10, tzinfo=UTC),
        attempt_count=2,
    )
    claims = [claimed, None]
    retries: list[dict[str, object]] = []

    async def fake_claim(*_args: object, **_kwargs: object):
        return claims.pop(0)

    async def fake_load(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(received_at=claimed.claimed_at)

    async def failing_process(*_args: object, **_kwargs: object) -> PaperCopyBatchResult:
        raise RuntimeError("exchange temporarily unavailable")

    async def fake_retry(*_args: object, **kwargs: object) -> bool:
        retries.append(kwargs)
        return True

    async def fake_publish(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(monitor_worker, "claim_next_live_copy_work", fake_claim)
    monkeypatch.setattr(monitor_worker, "load_claimed_live_copy_wallet_fill", fake_load)
    monkeypatch.setattr(
        monitor_worker,
        "paper_source_fill_from_wallet_fill",
        lambda _fill: {"id": "1"},
    )
    monkeypatch.setattr(monitor_worker, "process_live_copy_fills", failing_process)
    monkeypatch.setattr(monitor_worker, "retry_live_copy_work", fake_retry)
    monkeypatch.setattr(monitor_worker, "publish_event", fake_publish)
    stop_event = asyncio.Event()
    stop_event.set()
    intake_closed_event = asyncio.Event()
    intake_closed_event.set()
    runtime = monitor_worker.WorkerRuntimeState(role="trading", capabilities=("trading",))

    await monitor_worker.run_live_copy_work_loop(
        work_queue=asyncio.Queue(maxsize=1),
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        stop_event=stop_event,
        intake_closed_event=intake_closed_event,
        settings=SimpleNamespace(
            realtime_execution_claim_timeout_seconds=300,
            realtime_execution_retry_base_seconds=7,
        ),
        price_cache=None,
        runtime=runtime,
    )

    assert len(retries) == 1
    assert retries[0]["work_id"] == work_id
    assert retries[0]["owner"] == f"{runtime.instance_id}:live-copy-work"
    assert retries[0]["attempt_count"] == 2
    assert retries[0]["retry_base_seconds"] == 7
    assert isinstance(retries[0]["error"], RuntimeError)


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

    assert supervised == [
        "heartbeat",
        "realtime-execution",
        "live-copy-work",
        "realtime-subscription",
    ]
