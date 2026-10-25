"""Create candles table with bitemporal point-in-time schema.

Revision ID: 0001_create_candles
Revises:
Create Date: 2026-10-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_create_candles"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candles",
        sa.Column("instrument_id", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("timeframe", sa.Integer(), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_type", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("volume", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("tick_volume", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("spread", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("availability_confidence", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint(
            "instrument_id",
            "source_id",
            "timeframe",
            "open_time",
            "price_type",
            "revision",
            name="pk_candles",
        ),
    )

    op.create_index(
        "idx_candles_pit_lookup",
        "candles",
        [
            "source_id",
            "timeframe",
            "price_type",
            "open_time",
            "ingested_at",
            "available_at",
        ],
        unique=False,
    )

    op.create_index(
        "idx_candles_instrument_tf",
        "candles",
        ["instrument_id", "timeframe", "open_time"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_candles_instrument_tf", table_name="candles")
    op.drop_index("idx_candles_pit_lookup", table_name="candles")
    op.drop_table("candles")
