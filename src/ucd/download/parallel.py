"""Overlap bounded fetches without changing the source order of results."""

from collections import deque
from collections.abc import Callable, Generator, Iterable
from concurrent.futures import Future, ThreadPoolExecutor


def fetch_ordered[Item, Result](
    items: Iterable[Item], fetch: Callable[[int, Item], Result], workers: int
) -> Generator[tuple[int, Result]]:
    """Keep at most workers results in flight; consume them in input order.

    A bounded window prevents an entire book's decoded images accumulating
    behind one slow request. Results queued before an input error are delivered
    before that error is raised. Consumers own ordering and progress.
    On failure, cancel work not yet started and wait for running requests before
    their session or temporary directory is closed.
    """
    if workers < 1:
        raise ValueError("workers must be positive")
    if workers == 1:
        for index, item in enumerate(items):
            yield index, fetch(index, item)
        return

    executor = ThreadPoolExecutor(max_workers=workers)
    pending: deque[tuple[int, Future[Result]]] = deque()
    remaining = enumerate(items)
    input_error: Exception | None = None

    def submit_next() -> bool:
        nonlocal input_error
        if input_error is not None:
            return False
        try:
            entry = next(remaining, None)
        except Exception as exc:
            input_error = exc
            return False
        if entry is None:
            return False
        index, item = entry
        pending.append((index, executor.submit(fetch, index, item)))
        return True

    try:
        for _ in range(workers):
            if not submit_next():
                break

        while pending:
            index, future = pending.popleft()
            yield index, future.result()
            submit_next()

        if input_error is not None:
            raise input_error
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
