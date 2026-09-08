"""
Shared SQLAlchemy base and metadata for all models.
All models import Base from here to ensure they share the same MetaData.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
