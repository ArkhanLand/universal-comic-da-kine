from threading import Barrier, Event, Lock

import httpx
import pytest

from ucd.download.parallel import fetch_ordered
from ucd.input.libby_overdrive import LibbyOverDriveReadAdapter
from ucd.input.marvel_unlimited import MarvelUnlimitedAdapter


def test_overlap_order():
    barrier = Barrier(3)
    release = Event()
    finished = []
    lock = Lock()

    def fetch(index, item):
        barrier.wait(timeout=5)
        if index == 0:
            assert release.wait(timeout=5)
        with lock:
            finished.append(index)
            if len(finished) == 2:
                release.set()
        return item * 2

    assert list(fetch_ordered([10, 20, 30], fetch, 3)) == [(0, 20), (1, 40), (2, 60)]
    assert finished[-1] == 0


def test_bounded_window():
    started = []
    barrier = Barrier(3)

    def fetch(index, item):
        started.append(index)
        barrier.wait(timeout=5)
        return item

    results = fetch_ordered(list(range(20)), fetch, 3)
    try:
        assert next(results) == (0, 0)
        assert sorted(started) == [0, 1, 2]
    finally:
        results.close()


def test_worker_failure():
    started = []

    def fetch(index, item):
        started.append(index)
        if index == 0:
            raise ValueError("failed")
        return item

    with pytest.raises(ValueError, match="failed"):
        list(fetch_ordered(list(range(20)), fetch, 3))
    assert all(index < 3 for index in started)


def test_one_worker():
    started = []

    def fetch(index, item):
        started.append(index)
        return item

    results = fetch_ordered([10, 20], fetch, 1)
    assert next(results) == (0, 10)
    assert started == [0]
    results.close()


def test_empty_fetch():
    assert list(fetch_ordered([], lambda index, item: item, 4)) == []


@pytest.mark.parametrize("workers", [0, -1], ids=["zero", "negative"])
@pytest.mark.parametrize(
    "adapter", [LibbyOverDriveReadAdapter, MarvelUnlimitedAdapter], ids=["libby", "marvel"]
)
def test_bad_workers(adapter, workers):
    with pytest.raises(ValueError, match="positive"):
        adapter(workers=workers)


def test_sixteen_workers(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("request"))) as client:
        libby = LibbyOverDriveReadAdapter(
            store=object(),
            api_client=client,
            read_client=client,
            cache_dir=tmp_path,
            workers=16,
        )
        marvel = MarvelUnlimitedAdapter(client=client, cache_dir=tmp_path, workers=16)
        assert libby.workers == marvel.workers == 16
