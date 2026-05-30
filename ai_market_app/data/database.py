import os
from sqlalchemy import Column, Integer, String, Float, Text, Table, MetaData, create_engine
from sqlalchemy.exc import SQLAlchemyError

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///market_app.db")
engine = create_engine(DATABASE_URL, echo=False, future=True)
metadata = MetaData()

signals_table = Table(
    "signals",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("symbol", String, nullable=False),
    Column("timestamp", String, nullable=False),
    Column("signal", String, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("risk", String, nullable=False),
    Column("entry", Float),
    Column("stop_loss", Float),
    Column("target_1", Float),
    Column("target_2", Float),
    Column("reasons", Text)
)


def init_db() -> None:
    metadata.create_all(engine)


def save_signal(payload: dict) -> None:
    try:
        init_db()
        with engine.begin() as conn:
            conn.execute(
                signals_table.insert(),
                {
                    "symbol": payload.get("symbol", ""),
                    "timestamp": payload.get("timestamp", ""),
                    "signal": payload.get("signal", ""),
                    "confidence": float(payload.get("confidence", 0)),
                    "risk": payload.get("risk", ""),
                    "entry": payload.get("levels", {}).get("entry"),
                    "stop_loss": payload.get("levels", {}).get("stop_loss"),
                    "target_1": payload.get("levels", {}).get("target_1"),
                    "target_2": payload.get("levels", {}).get("target_2"),
                    "reasons": "\n".join(payload.get("reasons", []))
                }
            )
    except SQLAlchemyError:
        pass
