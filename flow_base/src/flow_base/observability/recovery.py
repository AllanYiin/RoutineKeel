"""Failure details for optional diagnostics, telemetry and post-commit notifications."""

import logging


class SecondaryOperation:
    """Keep a secondary failure available to its caller without masking the primary result."""

    def __init__(self, operation, *, catch=Exception):
        self.operation = operation
        self.catch = catch
        self.error = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, error, _traceback):
        if exc_type is None or not issubclass(exc_type, self.catch):
            return False
        self.error = error
        # Neither user inputs, exception messages nor frame locals belong in diagnostic logs.
        logger = logging.getLogger(__name__)
        if logger.isEnabledFor(logging.DEBUG):
            try:
                logger.debug("%s failed (%s)", self.operation, exc_type.__name__)
            except Exception:  # noqa: BLE001 -- a broken logging handler cannot replace the original failure
                return True
        return True
