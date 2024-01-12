"""Base class for plugwise node publisher."""

from __future__ import annotations
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from ...api import NodeFeature
from ...exceptions import SubscriptionError


@dataclass
class NodeSubscription:
    """Class to subscribe a callback to node events."""

    event: NodeFeature
    callback: Callable[[Any], Coroutine[Any, Any, None]] | Callable[
        [], Coroutine[Any, Any, None]
    ]


class NodePublisher():
    """Base Class to call awaitable of subscription when event happens."""

    _subscribers: dict[int, NodeSubscription] = {}
    _features: tuple[NodeFeature, ...] = ()

    def subscribe(self, subscription: NodeSubscription) -> int:
        """Add subscription and returns the id to unsubscribe later."""
        if subscription.event not in self._features:
            raise SubscriptionError(
                f"Subscription event {subscription.event} is not supported"
            )
        if id(subscription) in self._subscribers:
            raise SubscriptionError("Subscription already exists")
        self._subscribers[id(subscription)] = subscription
        return id(subscription)

    def subscribe_to_event(
        self,
        event: NodeFeature,
        callback: Callable[[Any], Coroutine[Any, Any, None]]
        | Callable[[], Coroutine[Any, Any, None]],
    ) -> int:
        """Subscribe callback to events."""
        return self.subscribe(
            NodeSubscription(
                event=event,
                callback=callback,
            )
        )

    def unsubscribe(self, subscription_id: int) -> bool:
        """Remove subscription. Returns True if unsubscribe was successful."""
        if subscription_id in self._subscribers:
            del self._subscribers[subscription_id]
            return True
        return False

    async def publish_event(self, event: NodeFeature, value: Any) -> None:
        """Publish feature to applicable subscribers."""
        if event not in self._features:
            return
        for subscription in list(self._subscribers.values()):
            if subscription.event != event:
                continue
            await subscription.callback(event, value)
