"""Domain errors mapped to HTTP statuses by the global exception handler."""


class DomainError(Exception):
    """Base for all domain-level failures. status_code drives the HTTP response."""

    status_code = 422

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TradeNotFoundError(DomainError):
    """404: the referenced Trade does not exist or does not belong to the user.

    Deliberately identical to "not found" for foreign ids: never leak whether
    another user's Trade exists.
    """

    status_code = 404


class JournalEntryNotFoundError(DomainError):
    status_code = 404


class WrongJournalStateError(DomainError):
    """409: the entry is not in the state required for the operation."""

    status_code = 409


class TradeCompletedError(DomainError):
    """409: executions/management changes are rejected after completion.

    Reopening/correction is a future explicit workflow, not a silent mutation.
    """

    status_code = 409


class OutcomeExistsError(DomainError):
    """409: a Trade has at most one outcome."""

    status_code = 409


class StaleValueError(DomainError):
    """409: old_value does not match the authoritative current value.

    Guards against two approvals racing on the same stop/target: the second
    approved payload was built from state the user did not review.
    """

    status_code = 409


class PositionViolationError(DomainError):
    """422: an execution would flip the position against the Trade direction."""

    status_code = 422


class MultiTradeNoteError(DomainError):
    """422: an approved payload must target exactly one Trade (MVP rule)."""

    status_code = 422


class AllowanceExhaustedError(DomainError):
    """402: the user's AI voice allowance for this period is used up.

    Raised before any expensive AI processing begins, per the monetization spec.
    """

    status_code = 402


class StorageUnavailableError(DomainError):
    """503: the audio could not be durably stored (e.g. Cloudinary down)."""

    status_code = 503