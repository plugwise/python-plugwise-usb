"""Base class for plugwise node publisher."""

from __future__ import annotations

from asyncio import gather
from collections.abc import Awaitable, Callable
from typing import Any

from ...api import NodeFeature


class FeaturePublisher():
    """Base Class to call awaitable of subscription when event happens."""

    _feature_update_subscribers: dict[
            Callable[[], None],
            tuple[Callable[[NodeEvent], Awaitable[None]], NodeEvent | None]
        ] = {}

    def subscribe_to_feature_update(
        self,
        node_feature_callback: Callable[
            [NodeFeature, Any], Awaitable[None]
        ],
        features: tuple[NodeFeature],
    ) -> Callable[[], None]:
        """
        Subscribe callback when specified NodeFeature state updates.
        Returns the function to be called to unsubscribe later.
        """
        def remove_subscription() -> None:
            """Remove stick event subscription."""
            self._feature_update_subscribers.pop(remove_subscription)

        self._feature_update_subscribers[
            remove_subscription
        ] = (node_feature_callback, features)
        return remove_subscription

    async def publish_feature_update_to_subscribers(
        self,
        feature: NodeFeature,
        state: Any,
    ) -> None:
        """Publish feature to applicable subscribers."""
        callback_list: list[Callable] = []
        for callback, filtered_features in list(
            self._feature_update_subscribers.values()
        ):
            if feature in filtered_features:
                callback_list.append(callback(feature, state))
        if len(callback_list) > 0:
            await gather(*callback_list)
