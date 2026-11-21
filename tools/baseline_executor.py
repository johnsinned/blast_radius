"""Synchronous process-pool stand-in used only while measuring the baseline."""
from concurrent.futures import Executor, Future
from multiprocessing.reduction import ForkingPickler


def round_trip(value):
    return ForkingPickler.loads(ForkingPickler.dumps(value))


class TracedExecutor(Executor):
    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kwargs):
        if initializer:
            initializer(*round_trip(initargs))

    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        try:
            call, call_args, call_kwargs = round_trip((fn, args, kwargs))
            future.set_result(round_trip(call(*call_args, **call_kwargs)))
        except BaseException as error:
            future.set_exception(error)
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        pass
