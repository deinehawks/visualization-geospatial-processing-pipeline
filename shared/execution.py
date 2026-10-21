"""Explicit per-invocation resource and persistence boundaries."""
from contextlib import nullcontext
from functools import wraps


class StatePersistenceError(RuntimeError):
    """State could not be committed; processing must not be replayed blindly."""


def resource_scope(owner, name):
    execution = getattr(owner, 'execution', None)
    return execution.resource(name) if execution is not None else nullcontext()


def transfer(owner, function, *args, **kwargs):
    with resource_scope(owner, 'bulk_transfer'):
        return function(*args, **kwargs)


def resource(name):
    def decorate(function):
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            with resource_scope(self, name):
                return function(self, *args, **kwargs)
        return wrapped
    return decorate


def remote_mutation(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        self._remote_mutation_started = False
        try:
            result = function(self, *args, **kwargs)
            self._remote_mutation_started = False
            return result
        except Exception as exc:
            execution = getattr(self, 'execution', None)
            if execution is None or not self._remote_mutation_started:
                raise
            from shared.stage_runner import StageRequiresRecovery
            execution.uncertain_remote(function.__name__)
            raise StageRequiresRecovery('WebODM mutation outcome needs reconciliation; automatic replay refused') from exc
    return wrapped
