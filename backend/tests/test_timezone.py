from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column, MetaData, Table, create_engine, insert, select

from app.api import _public_projection_updated_at
from app.schemas import UserResponse
from app.timezone import SHANGHAI_TZ, ShanghaiDateTime, as_shanghai


def test_shanghai_datetime_round_trip_uses_explicit_utc8() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    table = Table("timestamps", metadata, Column("value", ShanghaiDateTime(), nullable=False))
    metadata.create_all(engine)

    utc_value = datetime(2026, 8, 15, 15, 20, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(insert(table).values(value=utc_value))
        raw_value = connection.exec_driver_sql("SELECT value FROM timestamps").scalar_one()
        loaded_value = connection.execute(select(table.c.value)).scalar_one()

    assert raw_value == "2026-08-15 23:20:00.000000"
    assert loaded_value == datetime(2026, 8, 15, 23, 20, tzinfo=SHANGHAI_TZ)


def test_naive_orm_response_datetime_is_serialized_as_utc8() -> None:
    response = UserResponse(
        id="user-1",
        username="demo",
        role="viewer",
        is_active=True,
        created_at=datetime(2026, 8, 15, 15, 20),
    )

    assert response.model_dump(mode="json")["created_at"] == "2026-08-15T15:20:00+08:00"


def test_public_projection_datetime_is_serialized_as_utc8() -> None:
    class ScheduleFixture:
        published_at = datetime(2026, 8, 15, 15, 20, tzinfo=UTC)

    assert _public_projection_updated_at(ScheduleFixture()) == "2026-08-15T23:20:00+08:00"
    assert as_shanghai(ScheduleFixture.published_at) == datetime(
        2026, 8, 15, 23, 20, tzinfo=SHANGHAI_TZ
    )
