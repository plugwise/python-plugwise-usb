"""Plugwise Stick Exceptions."""


class PlugwiseException(Exception):
    """Base error class for this Plugwise library."""


class CacheError(PlugwiseException):
    """Cache error."""


class EnergyError(PlugwiseException):
    """Energy error."""


class FeatureError(PlugwiseException):
    """Feature error."""


class MessageError(PlugwiseException):
    """Message errors."""


class NodeError(PlugwiseException):
    """Node failed to execute request."""


class NodeTimeout(PlugwiseException):
    """No response from node."""


class StickError(PlugwiseException):
    """Error at USB stick connection."""


class StickFailed(PlugwiseException):
    """USB stick failed to accept request."""


class StickTimeout(PlugwiseException):
    """Response timed out from USB-Stick."""


class SubscriptionError(PlugwiseException):
    """Subscription Errors."""
