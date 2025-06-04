"""Helpers for Plugwise nodes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar, cast

from ...exceptions import NodeError


@dataclass
class EnergyCalibration:
    """Definition of a calibration for Plugwise devices (Circle, Stealth)."""

    gain_a: float
    gain_b: float
    off_noise: float
    off_tot: float


FuncT = TypeVar("FuncT", bound=Callable[..., Any])


def raise_not_loaded(func: FuncT) -> FuncT:
    """Raise NodeError when node is not loaded."""

    @wraps(func)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not args[0].is_loaded:
            raise NodeError(f"Node {args[0].mac} is not loaded yet")
        return func(*args, **kwargs)

    return cast(FuncT, decorated)
