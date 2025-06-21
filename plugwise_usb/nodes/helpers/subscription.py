"""Base class for plugwise node publisher."""

from __future__ import annotations

from asyncio import gather
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from ...api import NodeFeature


@dataclass
class NodeFeatureSubscription:
    """Subscription registration details for node feature."""

    callback_fn: Callable[[NodeFeature, Any], Coroutine[Any, Any, None]]
    features: tuple[NodeFeature, ...]


class FeaturePublisher:
    """Base Class to call awaitable of subscription when event happens."""

    def __init__(self) -> None:
        """Initialize FeaturePublisher Class."""
        self._feature_update_subscribers: dict[
            Callable[[], None],
            NodeFeatureSubscription,
        ] = {}

    def subscribe_to_feature_update(
        self,
        node_feature_callback: Callable[[NodeFeature, Any], Coroutine[Any, Any, None]],
        features: tuple[NodeFeature, ...],
    ) -> Callable[[], None]:
        """Subscribe callback when specified NodeFeature state updates.

        Returns the function to be called to unsubscribe later.
        """

        def remove_subscription() -> None:
            """Remove stick feature subscription."""
            self._feature_update_subscribers.pop(remove_subscription)

        self._feature_update_subscribers[remove_subscription] = NodeFeatureSubscription(
            node_feature_callback,
            features,
        )
        return remove_subscription

    async def publish_feature_update_to_subscribers(
        self,
        feature: NodeFeature,
        state: Any,
    ) -> None:
        """Publish feature to applicable subscribers."""
        callback_list: list[Coroutine[Any, Any, None]] = []
        for node_feature_subscription in list(
            self._feature_update_subscribers.values()
        ):
            if feature in node_feature_subscription.features:
                callback_list.append(
                    node_feature_subscription.callback_fn(feature, state)
                )
        if len(callback_list) > 0:
            await gather(*callback_list)
