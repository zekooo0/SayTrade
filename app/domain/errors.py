"""Domain errors mapped to HTTP statuses by the global exception handler."""


class DomainError(Exception):
    """Base for all domain-level failures. status_code drives the HTTP response."""

    status_code = 422

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TradeNotFoundError(DomainError):
    """404: the Trade does not exist or does not belong to the user.

    Deliberately identical for foreign ids: never leak whether another user's
    Trade exists.
    """

    status_code = 404


class NoteNotFoundError(DomainError):
    status_code = 404


class PortfolioNotFoundError(DomainError):
    """404: the Portfolio does not exist or does not belong to the user.

    Deliberately identical for foreign ids, like TradeNotFoundError.
    """

    status_code = 404


class WrongNoteStateError(DomainError):
    """409: the note is not in the state required for this operation."""

    status_code = 409


class TradeClosedError(DomainError):
    """409: a closed trade takes no more position changes.

    A note with no change (a review, a lesson) is still welcome on a closed
    trade; a new position opens a new trade.
    """

    status_code = 409


class IncompleteNoteError(DomainError):
    """422: the note is missing a value the change needs.

    Names the missing value so the trader can fill it in the review screen.
    """

    status_code = 422


class MarketRequiredError(DomainError):
    """422: the note has nowhere to go.

    Every note belongs to a trade to follow up on, or to a market to record into.
    Without one, the trade it opens would belong to no market — and markets never
    mix, so there is nothing sensible to do with it. The trader creates a market
    first; the app does not invent one.
    """

    status_code = 422


class PositionViolationError(DomainError):
    """422: a sale exceeds the shares held, or a change makes no sense."""

    status_code = 422


class AmbiguousTradeError(DomainError):
    """422: the note does not say which open trade it belongs to."""

    status_code = 422


class AllowanceExhaustedError(DomainError):
    """402: the AI voice allowance for this period is used up.

    Raised before any expensive AI processing begins.
    """

    status_code = 402


class TradeLimitReachedError(DomainError):
    """402: the plan's active-trade limit is reached.

    Raised at approval time, and only when the note would OPEN a trade — a
    follow-up note on a trade the trader already holds is never blocked.
    """

    status_code = 402


class StorageUnavailableError(DomainError):
    """503: the audio could not be durably stored (e.g. Cloudinary down)."""

    status_code = 503


class AuthUnavailableError(DomainError):
    """503: Clerk's public keys could not be fetched, so no token can be checked.

    Deliberately not a 401 — the session can be perfectly good, and a 401 would
    make the client sign the trader out because Clerk had a bad minute.
    """

    status_code = 503


class BillingUnavailableError(DomainError):
    """503: the payment provider is not configured, or could not be reached.

    Not a 4xx: the trader did nothing wrong. A checkout that cannot be made yet
    is a deployment fact, and the app shows the same "Pro is coming" line it
    showed before payments opened.
    """

    status_code = 503
