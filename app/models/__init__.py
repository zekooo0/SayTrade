# Import every model module here so that Alembic's autogenerate sees its tables.
# Without this, `alembic revision --autogenerate` silently produces empty migrations.
from app.models.change import CHANGE_KINDS, POSITION_KINDS, Change
from app.models.note import NOTE_SOURCES, NOTE_STATUSES, Note
from app.models.portfolio import Portfolio
from app.models.symbol import Symbol, SymbolAlias
from app.models.trade import Trade
from app.models.user import User

__all__ = [
    "CHANGE_KINDS",
    "NOTE_SOURCES",
    "NOTE_STATUSES",
    "POSITION_KINDS",
    "Change",
    "Note",
    "Portfolio",
    "Symbol",
    "SymbolAlias",
    "Trade",
    "User",
]
