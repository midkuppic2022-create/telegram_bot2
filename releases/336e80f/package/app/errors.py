class DomainError(Exception):
    """A user-facing domain validation error."""


class AccessDenied(DomainError):
    pass


class DuplicateOrderNotAllowed(DomainError):
    pass


class EntityNotFound(DomainError):
    pass


class EmptyExport(DomainError):
    pass
