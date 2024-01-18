"""
Manage the communication sessions towards the USB-Stick
"""
from __future__ import annotations

from asyncio import (
    CancelledError,
    InvalidStateError,
    PriorityQueue,
    Task,
    get_running_loop,
)
from collections.abc import Callable
from dataclasses import dataclass
import logging

from .manager import StickConnectionManager
from ..api import StickEvent
from ..exceptions import StickError
from ..messages.requests import PlugwiseRequest
from ..messages.responses import PlugwiseResponse

_LOGGER = logging.getLogger(__name__)


@dataclass
class RequestState:
    """Node hardware information."""
    session: bytes
    zigbee_address: int


class StickQueue:
    """Manage queue of all request sessions."""

    def __init__(self) -> None:
        """Initialize the message session controller."""
        self._stick: StickConnectionManager | None = None
        self._loop = get_running_loop()
        self._queue: PriorityQueue[PlugwiseRequest] = PriorityQueue()
        self._submit_worker_task: Task | None = None
        self._unsubscribe_connection_events: Callable[[], None] | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """Return the state of the queue"""
        return self._running

    def start(
        self,
        stick_connection_manager: StickConnectionManager
    ) -> None:
        """Start sending request from queue"""
        if self._running:
            raise StickError("Cannot start queue manager, already running")
        self._stick = stick_connection_manager
        if self._stick.is_connected:
            self._running = True
        self._unsubscribe_connection_events = (
            self._stick.subscribe_to_stick_events(
                self._handle_stick_event,
                (StickEvent.CONNECTED, StickEvent.DISCONNECTED)
            )
        )

    async def _handle_stick_event(self, event: StickEvent) -> None:
        """Handle events from stick"""
        if event is StickEvent.CONNECTED:
            self._running = True
        elif event is StickEvent.DISCONNECTED:
            self._running = False

    async def stop(self) -> None:
        """Stop sending from queue."""
        _LOGGER.debug("Stop queue")
        if self._unsubscribe_connection_events is not None:
            self._unsubscribe_connection_events()
        self._running = False
        self._stick = None
        if (
            self._submit_worker_task is not None and
            not self._submit_worker_task.done()
        ):
            self._submit_worker_task.cancel()
            try:
                await self._submit_worker_task.result()
            except (CancelledError, InvalidStateError):
                pass
        _LOGGER.debug("queue stopped")

    async def submit(
        self, request: PlugwiseRequest
    ) -> PlugwiseResponse:
        """
        Add request to queue and return the response of node
        Raises an error when something fails
        """
        if not self._running or self._stick is None:
            raise StickError(
                f"Cannot send message {request.__class__.__name__} for" +
                f"{request.mac_decoded} because queue manager is stopped"
            )

        await self._add_request_to_queue(request)
        try:
            response: PlugwiseResponse = await request.response_future()
        except BaseException as exception:  # [broad-exception-caught]
            raise exception.args[0]
        return response

    async def _add_request_to_queue(self, request: PlugwiseRequest) -> None:
        """Add request to send queue and return the session id."""
        await self._queue.put(request)
        self._start_submit_worker()

    def _start_submit_worker(self) -> None:
        """Start the submit worker if submit worker is not yet running"""
        if self._submit_worker_task is None or self._submit_worker_task.done():
            self._submit_worker_task = self._loop.create_task(
                self._submit_worker()
            )

    async def _submit_worker(self) -> None:
        """Send messages from queue at the order of priority."""
        while self._queue.qsize() > 0:
            # Get item with highest priority from queue first
            request = await self._queue.get()

            # Guard for incorrect futures
            if request.response is not None:
                _LOGGER.error(
                    "%s has already a response",
                    request.__class__.__name__,
                )
                break

            await self._stick.write_to_stick(request)
