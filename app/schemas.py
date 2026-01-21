from pydantic import BaseModel, Field


class SubscriptionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    amount_yen: int = Field(ge=0)
    billing_day: int = Field(ge=1, le=31)


class SubscriptionOut(BaseModel):
    id: int
    name: str
    amount_yen: int
    billing_day: int

    class Config:
        from_attributes = True
