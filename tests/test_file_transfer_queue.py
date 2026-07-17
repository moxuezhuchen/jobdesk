from jobdesk_app.core.file_transfer import TransferPlan, TransferQueue
from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus


def test_transfer_queue_processes_items_and_isolates_failures():
    queue = TransferQueue()
    ok = queue.add(TransferPlan(TransferDirection.upload, "a", "/r/a"))
    bad = queue.add(TransferPlan(TransferDirection.upload, "b", "/r/b"))
    after = queue.add(TransferPlan(TransferDirection.download, "c", "/r/c"))

    def runner(plan):
        if plan.local_path == "b":
            raise RuntimeError("boom")
        return TransferRecord(plan.direction, plan.local_path, plan.remote_path, status=TransferStatus.transferred)

    queue.run(runner)

    assert ok.status == TransferStatus.transferred
    assert bad.status == TransferStatus.failed
    assert bad.reason == "boom"
    assert after.status == TransferStatus.transferred


def test_transfer_queue_retries_failed_items_only():
    queue = TransferQueue()
    failed = queue.add(TransferPlan(TransferDirection.upload, "a", "/r/a"))
    done = queue.add(TransferPlan(TransferDirection.upload, "b", "/r/b"))
    failed.status = TransferStatus.failed
    done.status = TransferStatus.transferred

    queue.retry_failed(
        lambda plan: TransferRecord(
            plan.direction, plan.local_path, plan.remote_path, status=TransferStatus.transferred
        )
    )

    assert failed.status == TransferStatus.transferred
    assert done.status == TransferStatus.transferred


def test_transfer_queue_cancels_queued_item():
    queue = TransferQueue()
    item = queue.add(TransferPlan(TransferDirection.upload, "a", "/r/a"))

    queue.cancel(item.id)
    queue.run(
        lambda plan: TransferRecord(
            plan.direction, plan.local_path, plan.remote_path, status=TransferStatus.transferred
        )
    )

    assert item.status == TransferStatus.failed
    assert item.reason == "cancelled"
