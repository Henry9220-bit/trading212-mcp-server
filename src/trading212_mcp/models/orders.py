from datetime import datetime
from enum import StrEnum

from .base import ApiModel
from .metadata import Instrument


class OrderStatusEnum(StrEnum):
    LOCAL = "LOCAL"
    UNCONFIRMED = "UNCONFIRMED"
    CONFIRMED = "CONFIRMED"
    NEW = "NEW"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    REPLACING = "REPLACING"
    REPLACED = "REPLACED"


class OrderStrategyEnum(StrEnum):
    QUANTITY = "QUANTITY"
    VALUE = "VALUE"


class OrderTypeEnum(StrEnum):
    LIMIT = "LIMIT"
    STOP = "STOP"
    MARKET = "MARKET"
    STOP_LIMIT = "STOP_LIMIT"


class OrderSideEnum(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class PositionInitiatedFromEnum(StrEnum):
    API = "API"
    IOS = "IOS"
    ANDROID = "ANDROID"
    WEB = "WEB"
    SYSTEM = "SYSTEM"
    AUTOINVEST = "AUTOINVEST"
    INSTRUMENT_AUTOINVEST = "INSTRUMENT_AUTOINVEST"


class TimeValidityEnum(StrEnum):
    DAY = "DAY"
    GOOD_TILL_CANCEL = "GOOD_TILL_CANCEL"


class Order(ApiModel):
    createdAt: datetime | None = None
    currency: str | None = None
    extendedHours: bool | None = None
    filledQuantity: float | None = None
    filledValue: float | None = None
    id: int | None = None
    initiatedFrom: PositionInitiatedFromEnum | None = None
    instrument: Instrument | None = None
    limitPrice: float | None = None
    quantity: float | None = None
    side: OrderSideEnum | None = None
    status: OrderStatusEnum | None = None
    stopPrice: float | None = None
    strategy: OrderStrategyEnum | None = None
    ticker: str | None = None
    timeInForce: TimeValidityEnum | None = None
    type: OrderTypeEnum | None = None
    value: float | None = None
