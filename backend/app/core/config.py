from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "ReconcileAI"
    DATABASE_URL: str
    OPENAI_API_KEY: str = ""  # Not needed for reconciliation engine

    # ── Reconciliation Tolerances ──────────────────────────────────────────
    # PRICE_TOLERANCE_PERCENT: Maximum allowable percentage difference between
    # invoice and PO unit prices before flagging PRICE_MISMATCH.
    # 0 = exact match required.  Set to e.g. "2.0" for 2% tolerance.
    PRICE_TOLERANCE_PERCENT: Decimal = Decimal("0")

    # QUANTITY_TOLERANCE: Maximum allowable quantity difference (absolute units)
    # before flagging QUANTITY_OVERBILLING.  0 = exact match required.
    QUANTITY_TOLERANCE: Decimal = Decimal("0")

    # TAX_TOLERANCE_AMOUNT: Maximum allowable absolute difference (INR) between
    # invoice and PO tax amounts.  Accounts for small rounding differences.
    TAX_TOLERANCE_AMOUNT: Decimal = Decimal("1.00")

    # DISCOUNT_TOLERANCE_AMOUNT: Maximum allowable absolute difference (INR)
    # between invoice and PO discount amounts.
    DISCOUNT_TOLERANCE_AMOUNT: Decimal = Decimal("1.00")

    # TOTAL_TOLERANCE_AMOUNT: Maximum allowable absolute difference (INR) between
    # invoice and PO grand totals.  Also used for internal arithmetic checks.
    TOTAL_TOLERANCE_AMOUNT: Decimal = Decimal("1.00")

    # SEVERITY_HIGH_THRESHOLD: Financial impact (INR) above which an anomaly is
    # classified as HIGH severity.
    SEVERITY_HIGH_THRESHOLD: Decimal = Decimal("50000")

    # SEVERITY_MEDIUM_THRESHOLD: Financial impact (INR) above which an anomaly is
    # classified as MEDIUM severity (below HIGH threshold).
    SEVERITY_MEDIUM_THRESHOLD: Decimal = Decimal("5000")

    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore"
    )


settings = Settings()
