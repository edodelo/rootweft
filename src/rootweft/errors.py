"""Errors raised while reading and validating graph artifacts."""


class GraphError(ValueError):
    """Base class for graph artifact errors."""


class IncompatibleSchemaError(GraphError):
    """Raised when an artifact uses an unsupported schema version."""


class CorruptGraphError(GraphError):
    """Raised when an artifact cannot be decoded as a valid graph."""
