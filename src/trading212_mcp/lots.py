"""FIFO open-lot reconstruction from historical order fills.

Pure computation: no network, cache, clock or filesystem access. The caller
supplies the fetched history, the open positions and the current time.

Field sources are the Trading 212 OpenAPI snapshot in ``docs/api.json``:

* Direction comes from ``order.side`` (BUY / SELL) and size from
  ``abs(fill.quantity)``. The snapshot gives ``Fill.quantity`` no description
  and describes ``Order.filledQuantity`` only as "the number of shares that
  have been successfully executed". The one documented sign convention is for
  *placing* orders ("To execute a sell order, you must provide a negative value
  for the quantity parameter"). Whether a historical sell fill is reported as
  negative is therefore undocumented, so the sign is never trusted.
* ``Fill.price`` and ``FillWalletImpact.netValue`` have no description in the
  snapshot. ``Position.averagePricePaid`` is documented as "in instrument
  currency", and ``fill.price`` is treated the same way. Whether ``netValue``
  already includes fees and taxes is not stated, so it is reported as
  unconfirmed rather than guessed.
* ``Fill.type`` distinguishes TRADE from corporate actions and transfers
  (STOCK_SPLIT, FOP, SPIN_OFF, ...). Only TRADE fills are replayed. The others
  are reported, never converted into lots.

Lots are never invented or rescaled to match a position. When the replayed
quantity differs from the position, the lots are returned as computed and the
ticker is marked ``reconciled: false``.
"""

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel

from .models import (
    Fill,
    HistoricalOrder,
    Order,
    OrderSideEnum,
    OrderStatusEnum,
    Position,
)

RECONCILE_TOLERANCE = Decimal("0.000001")
TRADE = "TRADE"

FEES_NOTE = (
    "cost_account_currency is abs(fill.walletImpact.netValue), split by the "
    "share of the lot still held. Not confirmed whether netValue includes "
    "fees and taxes: docs/api.json does not describe the field."
)
CURRENCY_NOTE = (
    "price_native and cost_native are in the instrument currency "
    "(instrument_currency; GBX is pence, 100 GBX = 1 GBP). "
    "cost_account_currency is in account_currency."
)
METHOD_NOTE = (
    "Lots are rebuilt by replaying FILLED TRADE fills oldest first and matching "
    "sells against the oldest lots (FIFO). This is an analytical view, not a "
    "broker or tax record."
)
CAUSES_NOTE = (
    "Possible causes: stock split or consolidation, ticker change, a position "
    "transferred in without a buy order, another corporate action, history "
    "older than the fetched pages, or pie trades absent from history/orders."
)


class OpenLot(BaseModel):
    ticker: str
    filled_at: datetime
    quantity_remaining: float
    quantity_original: float
    price_native: float | None
    instrument_currency: str | None
    cost_native: float | None
    cost_account_currency: float | None
    account_currency: str | None
    fx_rate: float | None
    fill_id: int | None
    order_id: int | None
    holding_days: int


class TickerSummary(BaseModel):
    ticker: str
    total_quantity_from_lots: float
    position_quantity: float | None
    reconciled: bool
    difference: float | None
    weighted_avg_price_native: float | None
    oldest_lot_at: datetime | None
    newest_lot_at: datetime | None
    notes: list[str]


class OpenLotsReport(BaseModel):
    lots: list[OpenLot]
    summaries: list[TickerSummary]
    truncated: bool
    pages_fetched: int
    max_pages: int
    notes: list[str]


@dataclass
class _Lot:
    ticker: str
    filled_at: datetime
    original: Decimal
    remaining: Decimal
    price: Decimal | None
    net_value: Decimal | None
    instrument_currency: str | None
    account_currency: str | None
    fx_rate: float | None
    fill_id: int | None
    order_id: int | None


@dataclass
class _Ledger:
    lots: deque[_Lot] = field(default_factory=deque)
    unmatched_sells: Decimal = Decimal(0)
    non_trade: Counter[str] = field(default_factory=Counter)
    incomplete: int = 0
    unfilled_with_fill: Counter[str] = field(default_factory=Counter)


def _decimal(value: float | None) -> Decimal | None:
    # str() gives the shortest round-trip form, so 0.1 + 0.2 style binary
    # noise never enters the share arithmetic.
    return None if value is None else Decimal(str(value))


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _utc(value: datetime) -> datetime:
    # The API documents ISO 8601 date-times. A value without an offset is
    # read as UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _ticker(order: Order) -> str | None:
    return order.ticker or (order.instrument.ticker if order.instrument else None)


def build_open_lots(
    history: list[HistoricalOrder],
    positions: list[Position],
    *,
    now: datetime,
    truncated: bool,
    pages_fetched: int,
    max_pages: int,
) -> OpenLotsReport:
    """Replay fills into FIFO lots and reconcile each ticker with its position."""
    now = _utc(now)
    ledgers: dict[str, _Ledger] = {}
    unattributed = 0
    replay: list[tuple[datetime, int, int, str, Order, Fill]] = []

    for index, record in enumerate(history):
        order, fill = record.order, record.fill
        if order is None or fill is None:
            continue
        ticker = _ticker(order)
        filled = order.status == OrderStatusEnum.FILLED
        if not filled:
            # Only FILLED orders are replayed. A record that still carries an
            # executed quantity is surfaced because it may explain a mismatch.
            if ticker and fill.quantity:
                status = order.status.value if order.status else "UNKNOWN"
                ledgers.setdefault(ticker, _Ledger()).unfilled_with_fill[status] += 1
            continue
        if ticker is None:
            unattributed += 1
            continue
        ledger = ledgers.setdefault(ticker, _Ledger())
        if fill.type not in (None, TRADE):
            ledger.non_trade[fill.type] += 1
            continue
        if order.side is None or fill.filledAt is None or not fill.quantity:
            ledger.incomplete += 1
            continue
        replay.append((_utc(fill.filledAt), fill.id or 0, index, ticker, order, fill))

    # Oldest first; fills sharing a timestamp keep fill-ID order.
    replay.sort(key=lambda item: item[:3])
    for filled_at, _, _, ticker, order, fill in replay:
        ledger = ledgers[ticker]
        quantity = abs(Decimal(str(fill.quantity)))
        if order.side == OrderSideEnum.BUY:
            impact = fill.walletImpact
            ledger.lots.append(
                _Lot(
                    ticker=ticker,
                    filled_at=filled_at,
                    original=quantity,
                    remaining=quantity,
                    price=_decimal(fill.price),
                    net_value=_decimal(impact.netValue) if impact else None,
                    instrument_currency=(
                        order.instrument.currency if order.instrument else None
                    ),
                    account_currency=impact.currency if impact else None,
                    fx_rate=impact.fxRate if impact else None,
                    fill_id=fill.id,
                    order_id=order.id,
                )
            )
            continue
        while quantity > 0 and ledger.lots:
            oldest = ledger.lots[0]
            used = min(oldest.remaining, quantity)
            oldest.remaining -= used
            quantity -= used
            if oldest.remaining == 0:
                ledger.lots.popleft()
        ledger.unmatched_sells += quantity

    held: dict[str, Position] = {}
    for candidate in positions:
        if candidate.instrument and candidate.instrument.ticker:
            held[candidate.instrument.ticker] = candidate

    lots: list[OpenLot] = []
    summaries: list[TickerSummary] = []
    tickers = {t for t, ledger in ledgers.items() if ledger.lots} | set(held)
    for ticker in sorted(tickers):
        ledger = ledgers.get(ticker, _Ledger())
        position = held.get(ticker)
        fallback_currency = (
            position.instrument.currency if position and position.instrument else None
        )
        total = Decimal(0)
        priced_quantity = Decimal(0)
        priced_cost = Decimal(0)
        all_priced = True
        for lot in ledger.lots:
            total += lot.remaining
            if lot.price is None:
                all_priced = False
            else:
                priced_quantity += lot.remaining
                priced_cost += lot.price * lot.remaining
            lots.append(
                OpenLot(
                    ticker=ticker,
                    filled_at=lot.filled_at,
                    quantity_remaining=float(lot.remaining),
                    quantity_original=float(lot.original),
                    price_native=_number(lot.price),
                    instrument_currency=lot.instrument_currency or fallback_currency,
                    cost_native=_number(
                        lot.price * lot.remaining if lot.price is not None else None
                    ),
                    cost_account_currency=_number(
                        abs(lot.net_value) * lot.remaining / lot.original
                        if lot.net_value is not None
                        else None
                    ),
                    account_currency=lot.account_currency,
                    fx_rate=lot.fx_rate,
                    fill_id=lot.fill_id,
                    order_id=lot.order_id,
                    holding_days=max(0, (now - lot.filled_at).days),
                )
            )

        if position is None:
            position_quantity: Decimal | None = Decimal(0)
        else:
            position_quantity = _decimal(position.quantity)
        difference = None if position_quantity is None else total - position_quantity
        reconciled = difference is not None and abs(difference) <= RECONCILE_TOLERANCE

        notes: list[str] = []
        if ledger.non_trade:
            kinds = ", ".join(
                f"{kind} x{count}" for kind, count in sorted(ledger.non_trade.items())
            )
            notes.append(
                f"Non-trade fills were found and not replayed ({kinds}). Corporate "
                "actions and transfers are not applied to lots in this version."
            )
        if ledger.unmatched_sells > 0:
            notes.append(
                f"Sells exceed the known lots by {float(ledger.unmatched_sells)} "
                "shares, so earlier buys are missing from the fetched history."
            )
        if ledger.unfilled_with_fill:
            kinds = ", ".join(
                f"{kind} x{count}"
                for kind, count in sorted(ledger.unfilled_with_fill.items())
            )
            notes.append(
                f"Records with an executed quantity but a non-FILLED status were "
                f"ignored ({kinds})."
            )
        if ledger.incomplete:
            notes.append(
                f"{ledger.incomplete} FILLED record(s) lacked a side, fill time or "
                "quantity and were skipped."
            )
        if position is not None and position_quantity is None:
            notes.append("The position has no quantity in the API response.")
        if not reconciled:
            if position is None and ledger.lots:
                notes.append("Lots remain but there is no open position.")
            if position is not None and not ledger.lots:
                notes.append("No buy fills were found for this open position.")
            notes.append(CAUSES_NOTE)
            notes.append(f"truncated: {str(truncated).lower()}")

        summaries.append(
            TickerSummary(
                ticker=ticker,
                total_quantity_from_lots=float(total),
                position_quantity=_number(position_quantity),
                reconciled=reconciled,
                difference=_number(difference),
                weighted_avg_price_native=(
                    float(priced_cost / priced_quantity)
                    if all_priced and priced_quantity > 0
                    else None
                ),
                oldest_lot_at=ledger.lots[0].filled_at if ledger.lots else None,
                newest_lot_at=ledger.lots[-1].filled_at if ledger.lots else None,
                notes=notes,
            )
        )

    report_notes = [METHOD_NOTE, CURRENCY_NOTE, FEES_NOTE]
    if truncated:
        report_notes.append(
            f"truncated: true. History stopped at max_pages={max_pages}; older "
            "orders were not fetched, so lots may be missing or overstated."
        )
    else:
        report_notes.append("truncated: false. The full order history was fetched.")
    if unattributed:
        report_notes.append(
            f"{unattributed} FILLED record(s) had no ticker and were skipped."
        )
    return OpenLotsReport(
        lots=lots,
        summaries=summaries,
        truncated=truncated,
        pages_fetched=pages_fetched,
        max_pages=max_pages,
        notes=report_notes,
    )
