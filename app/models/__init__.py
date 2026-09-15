# Import every model module here so that Alembic's autogenerate sees its tables.
# Without this, `alembic revision --autogenerate` silently produces empty migrations.
from app.models.execution import SIDES, Execution
from app.models.journal_entry import JOURNAL_STATUSES, JOURNAL_TYPES, JournalEntry
from app.models.management_decision import DECISION_TYPES, ManagementDecision
from app.models.trade import DIRECTIONS, TRADE_STATUSES, Trade
from app.models.trade_outcome import OUTCOME_RESULTS, TradeOutcome
from app.models.usage_period import UsagePeriod
from app.models.user import User

__all__ = [
    "DECISION_TYPES",
    "DIRECTIONS",
    "JOURNAL_STATUSES",
    "JOURNAL_TYPES",
    "OUTCOME_RESULTS",
    "SIDES",
    "TRADE_STATUSES",
    "Execution",
    "JournalEntry",
    "ManagementDecision",
    "Trade",
    "TradeOutcome",
    "UsagePeriod",
    "User",
]