"""Receive data from USB-Stick.

Process incoming data stream from the Plugwise USB-Stick and
convert it into response messages.

Responsible to

  1. Collect and buffer raw data received from Stick: data_received()
  2. Convert raw data into response message: parse_data()
  3. Forward response message to the message subscribers

and publish detected connection status changes

  1. Notify status subscribers to connection state changes

"""

from __future__ import annotations

from asyncio import (
    Future,
    Lock,
    PriorityQueue,
    Protocol,
    Queue,
    Task,
    gather,
    get_running_loop,
    sleep,
)
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
import logging
from typing import Any, Final

from serial_asyncio_fast import SerialTransport

from ..api import StickEvent
from ..constants import MESSAGE_FOOTER, MESSAGE_HEADER
from ..exceptions import MessageError
from ..messages import Priority
from ..messages.responses import (
    BROADCAST_IDS,
    PlugwiseResponse,
    StickResponse,
    StickResponseType,
    get_message_object,
)

_LOGGER = logging.getLogger(__name__)
STICK_RECEIVER_EVENTS = (StickEvent.CONNECTED, StickEvent.DISCONNECTED)
CACHED_REQUESTS: Final = 50


@dataclass
class StickEventSubscription:
    """Subscription registration details for stick responses."""

    callback_fn: Callable[[StickEvent], Coroutine[Any, Any, None]]
    stick_events: tuple[StickEvent, ...]


@dataclass
class StickResponseSubscription:
    """Subscription registration details for stick responses."""

    callback_fn: Callable[[StickResponse], Coroutine[Any, Any, None]]
    seq_id: bytes | None
    stick_response_type: tuple[StickResponseType, ...] | None


@dataclass
class NodeResponseSubscription:
    """Subscription registration details for node responses."""

    callback_fn: Callable[[PlugwiseResponse], Coroutine[Any, Any, bool]]
    mac: bytes | None
    response_ids: tuple[bytes, ...] | None
    seq_id: bytes | None


class StickReceiver(Protocol):
    """Receive data from USB Stick connection and convert it into response messages."""

    def __init__(
        self,
        connected_future: Future[bool] | None = None,
    ) -> None:
        """Initialize instance of the USB Stick connection."""
        super().__init__()
        self._loop = get_running_loop()
        self._connected_future = connected_future
        self._transport: SerialTransport | None = None
        self._connection_state = False

        # Data processing
        self._buffer: bytes = bytes([])
        self._data_queue: Queue[bytes] = Queue()
        self._data_worker_task: Task[None] | None = None

        # Message processing
        self._processed_msgs = 0
        self._message_queue: PriorityQueue[PlugwiseResponse] = PriorityQueue()
        self._last_processed_messages: list[bytes] = []
        self._current_seq_id: bytes | None = None
        self._responses: dict[bytes, Callable[[PlugwiseResponse], None]] = {}
        self._message_worker_task: Task[None] | None = None
        self._delayed_processing_tasks: dict[bytes, Task[None]] = {}

        # Subscribers
        self._stick_subscription_lock = Lock()
        self._node_subscription_lock = Lock()

        self._stick_event_subscribers: dict[
            Callable[[], None], StickEventSubscription
        ] = {}
        self._stick_subscribers_for_requests: dict[
            Callable[[], None], StickResponseSubscription
        ] = {}
        self._stick_subscribers_for_responses: dict[
            Callable[[], None], StickResponseSubscription
        ] = {}

        self._node_response_subscribers: dict[
            Callable[[], None], NodeResponseSubscription
        ] = {}

    def connection_lost(self, exc: Exception | None = None) -> None:
        """Call when port was closed expectedly or unexpectedly."""
        _LOGGER.warning("Connection lost")
        if exc is not None:
            _LOGGER.warning("Connection to Plugwise USB-stick lost %s", exc)
        self._loop.create_task(self.close())
        if len(self._stick_event_subscribers) > 0:
            self._loop.create_task(
                self._notify_stick_event_subscribers(StickEvent.DISCONNECTED)
            )
        self._transport = None
        self._connection_state = False

    @property
    def processed_messages(self) -> int:
        """Return the number of processed messages."""
        return self._processed_msgs

    @property
    def is_connected(self) -> bool:
        """Return current connection state of the USB-Stick."""
        return self._connection_state

    def correct_processed_messages(self, correction: int) -> None:
        """Correct the number of processed messages."""
        self._processed_msgs += correction

    def connection_made(self, transport: SerialTransport) -> None:
        """Call when the serial connection to USB-Stick is established."""
        _LOGGER.info("Connection made")
        self._transport = transport
        if self._connected_future is not None and not self._connected_future.done():
            self._connected_future.set_result(True)
        self._connection_state = True
        if len(self._stick_event_subscribers) > 0:
            self._loop.create_task(
                self._notify_stick_event_subscribers(StickEvent.CONNECTED)
            )

    async def close(self) -> None:
        """Close connection."""
        await self._stop_running_tasks()
        if self._transport:
            self._transport.close()

    async def _stop_running_tasks(self) -> None:
        """Cancel and stop any running task."""
        for task in self._delayed_processing_tasks.values():
            task.cancel()
        if (
            self._message_worker_task is not None
            and not self._message_worker_task.done()
        ):
            cancel_response = StickResponse()
            cancel_response.priority = Priority.CANCEL
            await self._message_queue.put(cancel_response)
            await self._message_worker_task
            self._message_worker_task = None

        if self._data_worker_task is not None and not self._data_worker_task.done():
            await self._data_queue.put(b"FFFFFFFF")
            await self._data_worker_task
            self._data_worker_task = None

    # region Process incoming data

    def data_received(self, data: bytes) -> None:
        """Receive data from USB-Stick connection.

        This function is called by inherited asyncio.Protocol class
        """
        _LOGGER.debug("Received data from USB-Stick: %s", data)
        self._buffer += data
        if MESSAGE_FOOTER in self._buffer:
            data_of_messages = self._buffer.split(MESSAGE_FOOTER)
            for msg_data in data_of_messages[:-1]:
                # Ignore ASCII messages without a header and footer like:
                #    # SENDING PING UNICAST: Macid: ????????????????
                #    # HANDLE: 0x??
                #    # APSRequestNodeInfo
                if (header_index := msg_data.find(MESSAGE_HEADER)) != -1:
                    data = msg_data[header_index:]
                    self._put_data_in_queue(data)
            if len(data_of_messages) > 4:
                _LOGGER.debug(
                    "Reading %d messages at once from USB-Stick", len(data_of_messages)
                )
            self._buffer = data_of_messages[-1]  # whatever was left over

    def _put_data_in_queue(self, data: bytes) -> None:
        """Put raw message data in queue to be converted to messages."""
        self._data_queue.put_nowait(data)
        if self._data_worker_task is None or self._data_worker_task.done():
            self._data_worker_task = self._loop.create_task(
                self._data_queue_worker(), name="Plugwise data receiver queue worker"
            )

    async def _data_queue_worker(self) -> None:
        """Convert collected data into messages and place then im message queue."""
        _LOGGER.debug("Data queue worker started")
        while self.is_connected:
            if (data := await self._data_queue.get()) != b"FFFFFFFF":
                if (response := self.extract_message_from_data(data)) is not None:
                    await self._put_message_in_queue(response)
                self._data_queue.task_done()
            else:
                self._data_queue.task_done()
                return
            await sleep(0)
        _LOGGER.debug("Data queue worker stopped")

    def extract_message_from_data(self, msg_data: bytes) -> PlugwiseResponse | None:
        """Extract message from buffer."""
        identifier = msg_data[4:8]
        seq_id = msg_data[8:12]
        msg_data_length = len(msg_data)
        if (
            response := get_message_object(identifier, msg_data_length, seq_id)
        ) is None:
            _raw_msg_data_data = msg_data[2:][: msg_data_length - 4]
            _LOGGER.warning("Drop unknown message type %s", str(_raw_msg_data_data))
            return None

        # Populate response message object with data
        try:
            response.deserialize(msg_data, has_footer=False)
        except MessageError as err:
            _LOGGER.warning(err)
            return None

        _LOGGER.debug("Data %s converted into %s", msg_data, response)
        return response

    # endregion

    # region Process incoming messages

    async def _put_message_in_queue(
        self, response: PlugwiseResponse, delay: float = 0.0
    ) -> None:
        """Put message in queue to be processed."""
        if delay > 0.0:
            await sleep(delay)
        _LOGGER.debug("Add response to queue: %s", response)
        await self._message_queue.put(response)
        if self._message_worker_task is None or self._message_worker_task.done():
            _LOGGER.debug("Queue: start new worker-task")
            self._message_worker_task = self._loop.create_task(
                self._message_queue_worker(),
                name="Plugwise message receiver queue worker",
            )

    async def _message_queue_worker(self) -> None:
        """Process messages in receiver queue."""
        _LOGGER.debug("Message queue worker started")
        while self.is_connected:
            response: PlugwiseResponse = await self._message_queue.get()
            _LOGGER.debug("Priority: %s", response.priority)
            if response.priority == Priority.CANCEL:
                self._message_queue.task_done()
                return
            _LOGGER.debug("Message queue worker queue: %s", response)
            if isinstance(response, StickResponse):
                await self._notify_stick_subscribers(response)
            else:
                await self._notify_node_response_subscribers(response)
                self._processed_msgs += 1
            self._message_queue.task_done()
            await sleep(0)
        _LOGGER.debug("Message queue worker stopped")

    # endregion

    # region Stick

    def subscribe_to_stick_events(
        self,
        stick_event_callback: Callable[[StickEvent], Coroutine[Any, Any, None]],
        events: tuple[StickEvent, ...],
    ) -> Callable[[], None]:
        """Subscribe callback when specified StickEvent occurs.

        Returns the function to be called to unsubscribe later.
        """

        def remove_subscription() -> None:
            """Remove stick event subscription."""
            self._stick_event_subscribers.pop(remove_subscription)

        self._stick_event_subscribers[remove_subscription] = StickEventSubscription(
            stick_event_callback, events
        )
        return remove_subscription

    async def _notify_stick_event_subscribers(
        self,
        event: StickEvent,
    ) -> None:
        """Call callback for stick event subscribers."""
        callback_list: list[Awaitable[None]] = []
        for subscription in self._stick_event_subscribers.values():
            if event in subscription.stick_events:
                callback_list.append(subscription.callback_fn(event))
        if len(callback_list) > 0:
            await gather(*callback_list)

    async def subscribe_to_stick_responses(
        self,
        callback: Callable[[StickResponse], Coroutine[Any, Any, None]],
        seq_id: bytes | None = None,
        response_type: tuple[StickResponseType, ...] | None = None,
    ) -> Callable[[], None]:
        """Subscribe to response messages from stick."""

        def remove_subscription_for_requests() -> None:
            """Remove update listener."""
            self._stick_subscribers_for_requests.pop(remove_subscription_for_requests)

        def remove_subscription_for_responses() -> None:
            """Remove update listener."""
            self._stick_subscribers_for_responses.pop(remove_subscription_for_responses)

        if seq_id is None:
            await self._stick_subscription_lock.acquire()
            self._stick_subscribers_for_requests[remove_subscription_for_requests] = (
                StickResponseSubscription(callback, seq_id, response_type)
            )
            self._stick_subscription_lock.release()
            return remove_subscription_for_requests

        self._stick_subscribers_for_responses[remove_subscription_for_responses] = (
            StickResponseSubscription(callback, seq_id, response_type)
        )
        return remove_subscription_for_responses

    async def _notify_stick_subscribers(self, stick_response: StickResponse) -> None:
        """Call callback for all stick response message subscribers."""
        await self._stick_subscription_lock.acquire()
        for subscription in self._stick_subscribers_for_requests.values():
            if (
                subscription.seq_id is not None
                and subscription.seq_id != stick_response.seq_id
            ):
                continue
            if (
                subscription.stick_response_type is not None
                and stick_response.response_type not in subscription.stick_response_type
            ):
                continue
            _LOGGER.debug("Notify stick request subscriber for %s", stick_response)
            await subscription.callback_fn(stick_response)
        self._stick_subscription_lock.release()

        for subscription in list(self._stick_subscribers_for_responses.values()):
            if (
                subscription.seq_id is not None
                and subscription.seq_id != stick_response.seq_id
            ):
                continue
            if (
                subscription.stick_response_type is not None
                and stick_response.response_type not in subscription.stick_response_type
            ):
                continue
            _LOGGER.debug("Notify stick response subscriber for %s", stick_response)
            await subscription.callback_fn(stick_response)
        _LOGGER.debug(
            "Finished Notify stick response subscriber for %s", stick_response
        )

    # endregion
    # region node

    async def subscribe_to_node_responses(
        self,
        node_response_callback: Callable[[PlugwiseResponse], Coroutine[Any, Any, bool]],
        mac: bytes | None = None,
        message_ids: tuple[bytes, ...] | None = None,
        seq_id: bytes | None = None,
    ) -> Callable[[], None]:
        """Subscribe a awaitable callback to be called when a specific message is received.

        Returns function to unsubscribe.
        """
        await self._node_subscription_lock.acquire()

        def remove_listener() -> None:
            """Remove update listener."""
            _LOGGER.debug(
                "Node response subscriber removed: mac=%s, msg_idS=%s, seq_id=%s",
                mac,
                message_ids,
                seq_id,
            )
            self._node_response_subscribers.pop(remove_listener)

        self._node_response_subscribers[remove_listener] = NodeResponseSubscription(
            callback_fn=node_response_callback,
            mac=mac,
            response_ids=message_ids,
            seq_id=seq_id,
        )
        self._node_subscription_lock.release()
        _LOGGER.debug(
            "Node response subscriber added: mac=%s, msg_idS=%s, seq_id=%s",
            mac,
            message_ids,
            seq_id,
        )
        _LOGGER.debug("node subscription created for %s - %s", mac, seq_id)
        return remove_listener

    async def _notify_node_response_subscribers(
        self, node_response: PlugwiseResponse
    ) -> None:
        """Call callback for all node response message subscribers."""
        if node_response.seq_id is None:
            return

        if node_response.seq_id in self._last_processed_messages:
            _LOGGER.debug("Drop previously processed duplicate %s", node_response)
            return

        await self._node_subscription_lock.acquire()

        notify_tasks: list[Coroutine[Any, Any, bool]] = []
        for node_subscription in self._node_response_subscribers.values():
            if (
                node_subscription.mac is not None
                and node_subscription.mac != node_response.mac
            ):
                continue
            if (
                node_subscription.response_ids is not None
                and node_response.identifier not in node_subscription.response_ids
            ):
                continue
            if (
                node_subscription.seq_id is not None
                and node_subscription.seq_id != node_response.seq_id
            ):
                continue
            notify_tasks.append(node_subscription.callback_fn(node_response))

        self._node_subscription_lock.release()
        if len(notify_tasks) > 0:
            _LOGGER.info("Received %s %s", node_response, node_response.seq_id)
            if node_response.seq_id not in BROADCAST_IDS:
                self._last_processed_messages.append(node_response.seq_id)
            # Limit tracking to only the last appended request (FIFO)
            self._last_processed_messages = self._last_processed_messages[
                -CACHED_REQUESTS:
            ]

            # Cleanup pending task
            if node_response.seq_id in self._delayed_processing_tasks:
                del self._delayed_processing_tasks[node_response.seq_id]

            # execute callbacks
            _LOGGER.debug(
                "Notify node response subscribers (%s) about %s",
                len(notify_tasks),
                node_response,
            )
            task_result = await gather(*notify_tasks)

            # Log execution result for special cases
            if not all(task_result):
                _LOGGER.warning(
                    "Executed %s tasks (result=%s) for %s",
                    len(notify_tasks),
                    task_result,
                    node_response,
                )
            return

        if node_response.retries > 10:
            _LOGGER.warning(
                "No subscriber to handle %s after 10 retries",
                node_response,
            )
            return
        node_response.retries += 1
        self._delayed_processing_tasks[node_response.seq_id] = self._loop.create_task(
            self._put_message_in_queue(node_response, 0.1 * node_response.retries),
            name=f"Postpone subscription task for {node_response.seq_id!r} retry {node_response.retries}",
        )


# endregion
