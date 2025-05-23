"""Manage the communication sessions towards the USB-Stick."""
from __future__ import annotations

from asyncio import PriorityQueue, Task, get_running_loop
from collections.abc import Callable
from dataclasses import dataclass
import logging

from ..api import StickEvent
from ..exceptions import NodeTimeout, StickError, StickTimeout
from ..messages.requests import NodePingRequest, PlugwiseRequest, Priority
from ..messages.responses import PlugwiseResponse
from .manager import StickConnectionManager

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
        self._submit_queue: PriorityQueue[PlugwiseRequest] = PriorityQueue()
        self._submit_worker_task: Task | None = None
        self._unsubscribe_connection_events: Callable[[], None] | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """Return the state of the queue."""
        return self._running

    def start(
        self,
        stick_connection_manager: StickConnectionManager
    ) -> None:
        """Start sending request from queue."""
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
        """Handle events from stick."""
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
        if self._submit_worker_task is not None and not self._submit_worker_task.done():
            cancel_request = PlugwiseRequest(b"0000", None)
            cancel_request.priority = Priority.CANCEL
            await self._submit_queue.put(cancel_request)
        self._submit_worker_task = None
        self._stick = None
        _LOGGER.debug("queue stopped")

    async def submit(
        self, request: PlugwiseRequest
    ) -> PlugwiseResponse:
        """Add request to queue and return the response of node. Raises an error when something fails."""
        _LOGGER.debug("Queueing %s", request)
        while request.resend:
            if not self._running or self._stick is None:
                raise StickError(
                    f"Cannot send message {request.__class__.__name__} for" +
                    f"{request.mac_decoded} because queue manager is stopped"
                )
            await self._add_request_to_queue(request)
            try:
                response: PlugwiseResponse = await request.response_future()
                return response
            except (NodeTimeout, StickTimeout) as e:
                if request.resend:
                    _LOGGER.debug("%s, retrying", e)
                elif isinstance(request, NodePingRequest):
                    # For ping requests it is expected to receive timeouts, so lower log level
                    _LOGGER.debug("%s after %s attempts. Cancel request", e, request.max_retries)
                else:
                    _LOGGER.info("%s after %s attempts, cancel request", e, request.max_retries)
            except StickError as exception:
                _LOGGER.error(exception)
                raise StickError(
                    f"No response received for {request.__class__.__name__} " +
                    f"to {request.mac_decoded}"
                ) from exception
            except BaseException as exception:  # [broad-exception-caught]
                raise StickError(
                    f"No response received for {request.__class__.__name__} " +
                    f"to {request.mac_decoded}"
                ) from exception

        raise StickError(
            f"Failed to send {request.__class__.__name__} " +
            f"to node {request.mac_decoded}, maximum number " +
            f"of retries ({request.max_retries}) has been reached"
        )

    async def _add_request_to_queue(self, request: PlugwiseRequest) -> None:
        """Add request to send queue and return the session id."""
        await self._submit_queue.put(request)
        if self._submit_worker_task is None or self._submit_worker_task.done():
            self._submit_worker_task = self._loop.create_task(
                self._submit_worker()
            )

    async def _submit_worker(self) -> None:
        """Send messages from queue at the order of priority."""
        while self._running:
            request = await self._submit_queue.get()
            if request.priority == Priority.CANCEL:
                self._submit_queue.task_done()
                return
            await self._stick.write_to_stick(request)
            self._submit_queue.task_done()
