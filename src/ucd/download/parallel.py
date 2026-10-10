"""Overlap bounded fetches without changing the source order of results."""

from collections import deque
from collections.abc import Callable, Generator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor


def fetch_ordered[Item, Result](
    items: Sequence[Item], fetch: Callable[[int, Item], Result], workers: int
) -> Generator[tuple[int, Result]]:
    """Keep at most workers results in flight; consume them in input order.

    A bounded window prevents an entire book's decoded images accumulating
    behind one slow request. Consumers own shared state, receipts, and progress.
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
    try:
        for index, item in enumerate(items[:workers]):
            pending.append((index, executor.submit(fetch, index, item)))
        next_index = len(pending)

        while pending:
            index, future = pending.popleft()
            yield index, future.result()
            # Refill only after the caller consumes and records this result.
            if next_index < len(items):
                pending.append((next_index, executor.submit(fetch, next_index, items[next_index])))
                next_index += 1
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
