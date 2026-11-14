"""Public errors contain no upstream bodies, credentials, or request values."""


class Trading212Error(Exception):
    """An expected, safe-to-display API failure."""


class AuthenticationError(Trading212Error):
    pass


class RateLimitError(Trading212Error):
    pass


class UpstreamError(Trading212Error):
    pass


class NotFoundError(UpstreamError):
    """HTTP 404: the ticker, order or path is unknown to Trading 212."""


class InvalidResponseError(Trading212Error):
    pass


class RequestError(Trading212Error):
    pass
