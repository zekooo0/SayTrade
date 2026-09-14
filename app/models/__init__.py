# Import every model module here so that Alembic's autogenerate sees its tables.
# Without this, `alembic revision --autogenerate` silently produces empty migrations.
from app.models.execution import SIDES, Execution
from app.models.journal_entry import ENTRY_TYPES, JournalEntry
from app.models.management_decision import ManagementDecision
from app.models.opportunity import DIRECTIONS, STATUSES, Opportunity
from app.models.opportunity_outcome import RESULTS, OpportunityOutcome

__all__ = [
    "DIRECTIONS",
    "ENTRY_TYPES",
    "RESULTS",
    "SIDES",
    "STATUSES",
    "Execution",
    "JournalEntry",
    "ManagementDecision",
    "Opportunity",
    "OpportunityOutcome",
]
