"""Plugwise node devices."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..api import NodeEvent, NodeType, PlugwiseNode
from ..connection import StickController
from .circle import PlugwiseCircle
from .circle_plus import PlugwiseCirclePlus
from .scan import PlugwiseScan
from .sense import PlugwiseSense
from .stealth import PlugwiseStealth
from .switch import PlugwiseSwitch


def get_plugwise_node(  # noqa: PLR0911
    mac: str,
    address: int,
    controller: StickController,
    loaded_callback: Callable[[NodeEvent, str], Awaitable[None]],
    node_type: NodeType,
) -> PlugwiseNode | None:
    """Return an initialized plugwise node class based on given the node type."""
    if node_type == NodeType.CIRCLE_PLUS:
        return PlugwiseCirclePlus(
            mac,
            address,
            controller,
            loaded_callback,
        )
    if node_type == NodeType.CIRCLE:
        return PlugwiseCircle(
            mac,
            address,
            controller,
            loaded_callback,
        )
    if node_type == NodeType.SWITCH:
        return PlugwiseSwitch(
            mac,
            address,
            controller,
            loaded_callback,
        )
    if node_type == NodeType.SENSE:
        return PlugwiseSense(
            mac,
            address,
            controller,
            loaded_callback,
        )
    if node_type == NodeType.SCAN:
        return PlugwiseScan(
            mac,
            address,
            controller,
            loaded_callback,
        )
    if node_type == NodeType.STEALTH:
        return PlugwiseStealth(
            mac,
            address,
            controller,
            loaded_callback,
        )
    return None
