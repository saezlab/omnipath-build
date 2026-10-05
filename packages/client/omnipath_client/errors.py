"""Stable public errors shared by transport and snapshot validation."""


class ClientError(RuntimeError):
    """An unavailable artifact, inconsistent release, or invalid snapshot."""
