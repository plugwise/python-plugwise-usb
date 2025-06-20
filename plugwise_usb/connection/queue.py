"""Manage the communication sessions towards the USB-Stick."""

from __future__ import annotations

from asyncio import PriorityQueue, Task, get_running_loop, sleep
from collections.abc import Callable
from dataclasses import dataclass
import logging

from ..api import StickEvent
from ..exceptions import MessageError, NodeTimeout, StickError, StickTimeout
from ..messages import Priority
from ..messages.requests import NodePingRequest, PlugwiseCancelRequest, PlugwiseRequest
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
        self._submit_worker_task: Task[None] | None = None
        self._unsubscribe_connection_events: Callable[[], None] | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """Return the state of the queue."""
        return self._running

    def start(self, stick_connection_manager: StickConnectionManager) -> None:
        """Start sending request from queue."""
        if self._running:
            raise StickError("Cannot start queue manager, already running")
        self._stick = stick_connection_manager
        if self._stick.is_connected:
            self._running = True
        self._unsubscribe_connection_events = self._stick.subscribe_to_stick_events(
            self._handle_stick_event, (StickEvent.CONNECTED, StickEvent.DISCONNECTED)
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
            cancel_request = PlugwiseCancelRequest()
            await self._submit_queue.put(cancel_request)
            await self._submit_worker_task
        self._submit_worker_task = None
        self._stick = None
        _LOGGER.debug("queue stopped")

    async def submit(self, request: PlugwiseRequest) -> PlugwiseResponse | None:
        """Add request to queue and return the received node-response when applicable.

        Raises an error when something fails.
        """
        if request.waiting_for_response:
            raise MessageError(
                f"Cannot send message {request} which is currently waiting for response."
            )

        while request.resend:
            _LOGGER.debug("submit | start (%s) %s", request.retries_left, request)
            if not self._running or self._stick is None:
                raise StickError(
                    f"Cannot send message {request.__class__.__name__} for"
                    + f"{request.mac_decoded} because queue manager is stopped"
                )

            await self._add_request_to_queue(request)
            if request.no_response:
                return None

            try:
                response: PlugwiseResponse = await request.response_future()
                return response
            except (NodeTimeout, StickTimeout) as exc:
                if isinstance(request, NodePingRequest):
                    # For ping requests it is expected to receive timeouts, so lower log level
                    _LOGGER.debug(
                        "%s, cancel because timeout is expected for NodePingRequests",
                        exc,
                    )
                elif request.resend:
                    _LOGGER.debug("%s, retrying", exc)
                else:
                    _LOGGER.warning("%s, cancel request", exc)  # type: ignore[unreachable]
            except StickError as exc:
                _LOGGER.error(exc)
                self._stick.correct_received_messages(1)
                raise StickError(
                    f"No response received for {request.__class__.__name__} "
                    + f"to {request.mac_decoded}"
                ) from exc
            except BaseException as exc:
                self._stick.correct_received_messages(1)
                raise StickError(
                    f"No response received for {request.__class__.__name__} "
                    + f"to {request.mac_decoded}"
                ) from exc

        return None

    async def _add_request_to_queue(self, request: PlugwiseRequest) -> None:
        """Add request to send queue."""
        _LOGGER.debug("Add request to queue: %s", request)
        await self._submit_queue.put(request)
        if self._submit_worker_task is None or self._submit_worker_task.done():
            self._submit_worker_task = self._loop.create_task(
                self._send_queue_worker(), name="Send queue worker"
            )

    async def _send_queue_worker(self) -> None:
        """Send messages from queue at the order of priority."""
        _LOGGER.debug("Send_queue_worker started")
        while self._running and self._stick is not None:
            request = await self._submit_queue.get()
            _LOGGER.debug("Sending from send queue %s", request)
            if request.priority == Priority.CANCEL:
                self._submit_queue.task_done()
                return

            if self._stick.queue_depth > 3:
                await sleep(0.125)
                if self._stick.queue_depth > 3:
                    _LOGGER.warning(
                        "Awaiting plugwise responses %d", self._stick.queue_depth
                    )

            await self._stick.write_to_stick(request)
            self._submit_queue.task_done()

            _LOGGER.debug("Sent from queue %s", request)
        _LOGGER.debug("Send_queue_worker stopped")
