"""Cooperative semantic hooks on dedicated pipes, independent of stdout."""

from __future__ import annotations

import json
import os

from .model import Boundary


class Probe:
    def __init__(self) -> None:
        self._events = int(os.environ["CRASHFIXTURE_EVENTS_FD"])
        self._acks = int(os.environ["CRASHFIXTURE_ACKS_FD"])
        self.token = os.environ["CRASHFIXTURE_TOKEN"]
        os.set_inheritable(self._events, False)
        os.set_inheritable(self._acks, False)

    def hit(self, boundary: Boundary) -> None:
        """Report a committed boundary and park until ack or actual SIGKILL.

        Call outside any database transaction, after the commit returns. A hook
        is instrumentation, not a fault witness. The parent observes wait status.
        """
        if not isinstance(boundary, Boundary):
            raise ValueError("boundary must be a Boundary")
        event = (
            json.dumps(
                {"boundary": boundary.value, "pid": os.getpid(), "token": self.token}
            ).encode()
            + b"\n"
        )
        if os.write(self._events, event) != len(event):
            raise RuntimeError("short checkpoint write")
        if os.read(self._acks, 1) != b"+":
            raise RuntimeError("checkpoint acknowledgement missing")
