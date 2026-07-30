import threading
import time

from songcoach import stem_queue


def test_enqueue_runs_serially_in_fifo_order(monkeypatch):
    order = []
    concurrent = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake_run(job_id):
        with lock:
            concurrent["now"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["now"])
        time.sleep(0.02)
        order.append(job_id)
        with lock:
            concurrent["now"] -= 1

    monkeypatch.setattr(stem_queue, "_run_job", fake_run)

    for jid in ["a", "b", "c", "d"]:
        stem_queue.enqueue(jid)
    stem_queue._queue.join()

    assert order == ["a", "b", "c", "d"]      # FIFO
    assert concurrent["max"] == 1             # never two at once


def test_worker_survives_a_failing_job(monkeypatch):
    seen = []

    def fake_run(job_id):
        seen.append(job_id)
        if job_id == "boom":
            raise RuntimeError("kaboom")

    monkeypatch.setattr(stem_queue, "_run_job", fake_run)
    for jid in ["boom", "after"]:
        stem_queue.enqueue(jid)
    stem_queue._queue.join()
    assert seen == ["boom", "after"]          # a failure doesn't kill the worker
