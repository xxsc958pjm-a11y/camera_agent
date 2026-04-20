class PTUError(Exception):
    """Base exception for the FLIR PTU agent project."""


class PTUConnectionError(PTUError):
    """Raised when the PTU web interface cannot be reached."""


class PTUDiscoveryError(PTUError):
    """Raised when discovery could not complete successfully."""


class PTUControlNotImplementedError(PTUError):
    """Raised when no verified HTTP control interface is available."""


class PTUResponseParseError(PTUError):
    """Raised when a PTU HTTP response could not be parsed as expected."""
