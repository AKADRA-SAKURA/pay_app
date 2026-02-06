from pydantic import BaseModel, Field


class SubscriptionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    amount_yen: int = Field(ge=0)
    billing_day: int = Field(ge=1, le=31)
    freq: str = Field(default="monthly")
    interval_months: int = Field(default=1, ge=1)
    billing_month: int = Field(default=1, ge=1, le=12)
    payment_method: str = Field(default="bank")
    account_id: int | None = None
    card_id: int | None = None


class SubscriptionOut(BaseModel):
    id: int
    name: str
    amount_yen: int
    billing_day: int
    freq: str
    interval_months: int
    billing_month: int
    payment_method: str
    account_id: int | None = None
    card_id: int | None = None

    class Config:
        from_attributes = True
