"""
test_store_async.py — background saving in student_store (no Dropbox needed).

Dropbox is replaced by a fake, deliberately slow upload so the tests can check that:
  - save_async() returns immediately instead of waiting on the upload
  - a burst of saves for one student is coalesced into few uploads, and the NEWEST wins
  - two students' saves don't block or overwrite each other
  - a failed upload is reported through save_status() so the app can warn honestly

Run from the repo root:
    python tests/test_store_async.py
"""

import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import student_store as store  # noqa: E402


class _PlainFernet:
    """Stand-in for Fernet: 'encrypts' by passing bytes through, so payloads stay readable."""
    def encrypt(self, b):
        return b

    def decrypt(self, b):
        return b


def _setup(upload):
    store.ENABLED = True
    store._FERNET = _PlainFernet()
    store._upload = upload
    with store._cv:
        store._pending.clear()
        store._inflight.clear()
        store._status.clear()


def test_save_async_returns_without_waiting():
    def slow_upload(path, data):
        time.sleep(1.0)
    _setup(slow_upload)
    t = time.time()
    assert store.save_async("toc", "alice", {"x": 1})
    assert time.time() - t < 0.2, "save_async blocked on the upload"
    assert store.save_status("alice")["pending"] is True
    assert store.flush(10)
    s = store.save_status("alice")
    assert s["ok"] and not s["pending"] and s["ts"]


def test_burst_is_coalesced_and_newest_wins():
    uploads = []
    lock = threading.Lock()

    def upload(path, data):
        time.sleep(0.3)
        with lock:
            uploads.append((path, json.loads(data.decode())["n"]))
    _setup(upload)
    for n in range(10):
        store.save_async("toc", "bob", {"n": n})
        time.sleep(0.02)
    assert store.flush(10)
    bob = [n for p, n in uploads if "bob" in p]
    assert bob[-1] == 9, f"newest snapshot must be the last one written, got {bob}"
    assert len(bob) <= 3, f"10 rapid saves should coalesce, got {len(bob)} uploads"


def test_students_are_independent():
    done = {}

    def upload(path, data):
        time.sleep(0.4)
        done[path] = json.loads(data.decode())["who"]
    _setup(upload)
    t = time.time()
    for who in ("s1", "s2", "s3", "s4"):
        store.save_async("toc", who, {"who": who})
    assert store.flush(10)
    assert sorted(done.values()) == ["s1", "s2", "s3", "s4"]
    assert time.time() - t < 1.6, "uploads for different students should run in parallel"


def test_failure_is_reported():
    def failing(path, data):
        raise RuntimeError("dropbox down")
    _setup(failing)
    store.save_async("toc", "carol", {"x": 1})
    assert store.flush(10)
    assert store.save_status("carol")["ok"] is False

    _setup(lambda p, d: None)          # next save succeeds -> status recovers
    store.save_async("toc", "carol", {"x": 2})
    assert store.flush(10)
    assert store.save_status("carol")["ok"] is True


def test_disabled_store_is_a_noop():
    _setup(lambda p, d: None)
    store.ENABLED = False
    assert store.save_async("toc", "dave", {"x": 1}) is False
    assert store.save_status("dave") is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:      # noqa: BLE001
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed} passed, {failed} failed, {len(tests)} total")
    sys.exit(1 if failed else 0)
