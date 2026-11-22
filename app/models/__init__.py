# Import every model module here so that Alembic's autogenerate sees its tables.
# Without this, `alembic revision --autogenerate` silently produces empty migrations.
from app.models.change import CHANGE_KINDS, POSITION_KINDS, Change
from app.models.note import NOTE_SOURCES, NOTE_STATUSES, Note
from app.models.portfolio import Portfolio
from app.models.prediction import PREDICTION_RESULTS, Prediction
from app.models.subscription import Subscription
from app.models.symbol import Symbol, SymbolAlias
from app.models.trade import Trade
from app.models.user import User
from app.models.waitlist import WaitlistSignup
from app.models.weekly_review import WeeklyReview

__all__ = [
    "CHANGE_KINDS",
    "NOTE_SOURCES",
    "NOTE_STATUSES",
    "POSITION_KINDS",
    "PREDICTION_RESULTS",
    "Change",
    "Note",
    "Portfolio",
    "Prediction",
    "Subscription",
    "Symbol",
    "SymbolAlias",
    "Trade",
    "User",
    "WaitlistSignup",
    "WeeklyReview",
]
