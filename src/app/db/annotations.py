"""Reusable SQLAlchemy column annotations.

Keeping common column definitions here makes the ORM models declarative and
ensures every timestamp is timezone-aware and every JSON column has the same
nullability/default behavior.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import mapped_column

int_pk = Annotated[int, mapped_column(primary_key=True, autoincrement=True)]
# Telegram ids exceed 32-bit ints, hence BigInteger and a unique index.
tg_id = Annotated[int, mapped_column(BigInteger, unique=True, index=True)]

str_32 = Annotated[str, mapped_column(String(32))]
str_64 = Annotated[str, mapped_column(String(64))]
str_128 = Annotated[str, mapped_column(String(128))]
str_256 = Annotated[str, mapped_column(String(256))]
str_512 = Annotated[str, mapped_column(String(512))]

text = Annotated[str, mapped_column(Text)]
json_dict = Annotated[dict[str, Any], mapped_column(JSON)]
json_dict_not_null = Annotated[dict[str, Any], mapped_column(JSON, default=dict, nullable=False)]

datetime_utc = Annotated[datetime, mapped_column(DateTime(timezone=True))]
datetime_utc_nullable = Annotated[datetime | None, mapped_column(DateTime(timezone=True))]

bool_default_false = Annotated[bool, mapped_column(Boolean, default=False, nullable=False)]
bool_default_true = Annotated[bool, mapped_column(Boolean, default=True, nullable=False)]


def str_enum(enum_cls: type[StrEnum], *, length: int = 32) -> SAEnum:
    """Store a StrEnum as VARCHAR instead of a native database enum.

    ``native_enum=False`` keeps SQLite simple and makes adding a new enum value
    a code-only change; ``validate_strings`` still rejects unknown values on
    write.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        validate_strings=True,
    )
