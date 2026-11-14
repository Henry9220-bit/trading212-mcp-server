from datetime import datetime
from enum import StrEnum

from .base import ApiModel
from .metadata import Instrument
from .orders import Order


class HistoryTransactionTypeEnum(StrEnum):
    WITHDRAW = "WITHDRAW"
    DEPOSIT = "DEPOSIT"
    FEE = "FEE"
    TRANSFER = "TRANSFER"
    INTEREST_ON_FREE_CASH = "INTEREST_ON_FREE_CASH"
    LENDING_INTEREST = "LENDING_INTEREST"


class ReportResponseStatusEnum(StrEnum):
    Queued = "Queued"
    Processing = "Processing"
    Running = "Running"
    Canceled = "Canceled"
    Failed = "Failed"
    Finished = "Finished"


class Tax(ApiModel):
    fillId: str | None = None
    name: str | None = None
    quantity: float | None = None
    timeCharged: datetime | None = None
    chargedAt: datetime | None = None
    currency: str | None = None


class FillWalletImpact(ApiModel):
    currency: str | None = None
    fxRate: float | None = None
    netValue: float | None = None
    realisedProfitLoss: float | None = None
    taxes: list[Tax] | None = None


class Fill(ApiModel):
    filledAt: datetime | None = None
    id: int | None = None
    price: float | None = None
    quantity: float | None = None
    tradingMethod: str | None = None
    type: str | None = None
    walletImpact: FillWalletImpact | None = None


class HistoricalOrder(ApiModel):
    fill: Fill | None = None
    order: Order | None = None


class HistoryDividendItem(ApiModel):
    amount: float | None = None
    amountInEuro: float | None = None
    currency: str | None = None
    grossAmountPerShare: float | None = None
    instrument: Instrument | None = None
    paidOn: datetime | None = None
    quantity: float | None = None
    reference: str | None = None
    ticker: str | None = None
    tickerCurrency: str | None = None
    type: str | None = None


class HistoryTransactionItem(ApiModel):
    amount: float | None = None
    currency: str | None = None
    dateTime: datetime | None = None
    reference: str | None = None
    type: HistoryTransactionTypeEnum | None = None


class PaginatedResponseHistoricalOrder(ApiModel):
    items: list[HistoricalOrder]
    nextPagePath: str | None = None


class PaginatedResponseHistoryDividendItem(ApiModel):
    items: list[HistoryDividendItem]
    nextPagePath: str | None = None


class PaginatedResponseHistoryTransactionItem(ApiModel):
    items: list[HistoryTransactionItem]
    nextPagePath: str | None = None


class ReportDataIncluded(ApiModel):
    includeDividends: bool | None = True
    includeInterest: bool | None = True
    includeOrders: bool | None = True
    includeTransactions: bool | None = True


class ReportResponse(ApiModel):
    dataIncluded: ReportDataIncluded | None = None
    downloadLink: str | None = None
    reportId: int | None = None
    status: ReportResponseStatusEnum | None = None
    timeFrom: datetime | None = None
    timeTo: datetime | None = None
