class SparkError(Exception):
    exit_code = 1


class ConfigError(SparkError):
    exit_code = 2


class PathEscapeError(SparkError):
    """Raised when a tool path resolves outside the workdir."""


class UserAbortError(SparkError):
    exit_code = 3
