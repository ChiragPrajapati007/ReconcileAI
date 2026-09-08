from app.db.base import Base  # noqa: F401
from app.db.session import engine  # noqa: F401
import app.models  # noqa: F401 — ensures all models are registered with Base
