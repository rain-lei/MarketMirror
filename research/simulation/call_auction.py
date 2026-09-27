"""Finite-inventory uniform call auction with integer settlement and session locks.

These are explicit model rules, not an implementation of a real exchange's
regulations. No external party absorbs unmatched orders or creates assets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

VERSION = "finite-call-auction-v1"


def _integer(value: Any, lower: int, name: str) -> None:
    if type(value) is not int or value < lower:
        raise ValueError(f"{name} must be an integer at least {lower}")


def fee_minor(notional_minor: int, fee_bps: int) -> int:
    """One rounded-up fee per filled order, irrespective of counterparty splits."""
    return (notional_minor * fee_bps + 9999) // 10000


@dataclass
class AuctionAccount:
    cash_minor: int
    shares: int
    sellable_shares: int

    def validate(self) -> None:
        for name in ("cash_minor", "shares", "sellable_shares"):
            _integer(getattr(self, name), 0, name)
        if self.sellable_shares > self.shares:
            raise ValueError("sellable inventory exceeds owned shares")


@dataclass(frozen=True)
class LimitOrder:
    order_id: str
    owner: str
    side: str
    quantity: int
    limit_price_minor: int
    sequence: int


class CallAuction:
    """One day-order call per session; buys become sellable next session.

    Price maximizes executable volume, then minimizes absolute imbalance,
    then distance to the last price, then the lower tick. Allocation uses
    better limit price followed by explicit sequence, with no self-crossing
    because each owner can submit only one side per session.
    """

    def __init__(self, accounts: dict[str, AuctionAccount], price_minor: int,
                 lot_size: int, tick_minor: int, fee_bps: int, price_band_bps: int):
        for value, minimum, name in ((price_minor, 1, "price_minor"), (lot_size, 1, "lot_size"),
                                      (tick_minor, 1, "tick_minor"), (fee_bps, 0, "fee_bps"),
                                      (price_band_bps, 0, "price_band_bps")):
            _integer(value, minimum, name)
        if fee_bps > 10000 or price_band_bps > 5000 or price_minor % tick_minor:
            raise ValueError("invalid fees, price band or initial tick")
        if not accounts or any(not isinstance(name, str) or not name for name in accounts):
            raise ValueError("auction requires explicitly named accounts")
        for account in accounts.values():
            account.validate()
        self.accounts = {name: AuctionAccount(**asdict(account)) for name, account in accounts.items()}
        self.price_minor, self.lot_size, self.tick_minor = price_minor, lot_size, tick_minor
        self.fee_bps, self.price_band_bps, self.session = fee_bps, price_band_bps, -1
        self.initial_cash_minor = sum(account.cash_minor for account in self.accounts.values())
        self.initial_shares = sum(account.shares for account in self.accounts.values())
        self.fee_pool_minor = 0

    def price_bounds(self) -> tuple[int, int]:
        lower = max(self.tick_minor, (self.price_minor * (10000 - self.price_band_bps)
                                     + 10000 * self.tick_minor - 1) // (10000 * self.tick_minor) * self.tick_minor)
        upper = self.price_minor * (10000 + self.price_band_bps) // (10000 * self.tick_minor) * self.tick_minor
        return lower, upper

    def _validate_orders(self, orders: list[LimitOrder]) -> None:
        if not isinstance(orders, list) or any(not isinstance(order, LimitOrder) for order in orders):
            raise ValueError("auction orders must be a list of LimitOrder records")
        ids, owners, sequences = set(), set(), set()
        for order in orders:
            if (not isinstance(order.order_id, str) or not order.order_id or order.order_id in ids
                    or order.owner not in self.accounts or order.owner in owners
                    or order.side not in {"buy", "sell"}):
                raise ValueError("orders require unique IDs and one side per known owner")
            _integer(order.quantity, 1, "order quantity")
            _integer(order.limit_price_minor, 1, "limit price")
            _integer(order.sequence, 0, "order sequence")
            if (order.quantity % self.lot_size or order.limit_price_minor % self.tick_minor
                    or order.sequence in sequences):
                raise ValueError("orders require valid lots, ticks and unique sequences")
            ids.add(order.order_id)
            owners.add(order.owner)
            sequences.add(order.sequence)

    def clear(self, session: int, orders: list[LimitOrder], execution_available: bool = True) -> dict:
        _integer(session, 0, "session")
        if session != self.session + 1 or type(execution_available) is not bool:
            raise ValueError("auction session must advance exactly once and availability must be boolean")
        self._validate_orders(orders)  # Invalid input cannot advance or mutate the venue.
        if self.session >= 0:
            for account in self.accounts.values():
                account.sellable_shares = account.shares
        lower, upper = self.price_bounds()
        accepted, order_rows = [], []
        for order in sorted(orders, key=lambda row: row.sequence):
            account = self.accounts[order.owner]
            reasons = []
            quantity = order.quantity
            if not execution_available:
                quantity = 0
                reasons.append("trading_halted")
            elif not lower <= order.limit_price_minor <= upper:
                quantity = 0
                reasons.append("price_outside_model_band")
            elif order.side == "buy":
                affordable_lots = account.cash_minor * 10000 // (
                    order.limit_price_minor * self.lot_size * (10000 + self.fee_bps))
                quantity = min(quantity, affordable_lots * self.lot_size)
                if quantity != order.quantity:
                    reasons.append("cash_and_fee_reservation")
            else:
                quantity = min(quantity, account.sellable_shares // self.lot_size * self.lot_size)
                if quantity != order.quantity:
                    reasons.append("sellable_inventory")
            record = {**asdict(order), "accepted_quantity": quantity,
                      "filled_quantity": 0, "fee_minor": 0, "reasons": reasons}
            order_rows.append(record)
            if quantity:
                accepted.append(record)
        candidates = {self.price_minor}
        for order in accepted:
            for offset in (-self.tick_minor, 0, self.tick_minor):
                candidate = order["limit_price_minor"] + offset
                if lower <= candidate <= upper:
                    candidates.add(candidate)
        evaluations = []
        for candidate in sorted(candidates):
            demand = sum(order["accepted_quantity"] for order in accepted
                         if order["side"] == "buy" and order["limit_price_minor"] >= candidate)
            supply = sum(order["accepted_quantity"] for order in accepted
                         if order["side"] == "sell" and order["limit_price_minor"] <= candidate)
            evaluations.append((candidate, min(demand, supply), demand - supply))
        clearing_price, volume, imbalance = min(evaluations, key=lambda row: (
            -row[1], abs(row[2]), abs(row[0] - self.price_minor), row[0]))
        if volume == 0:
            clearing_price = self.price_minor  # No transaction cannot set a new price.
            imbalance = next(row[2] for row in evaluations if row[0] == self.price_minor)
        buyers = sorted((row for row in accepted if row["side"] == "buy"
                         and row["limit_price_minor"] >= clearing_price),
                        key=lambda row: (-row["limit_price_minor"], row["sequence"]))
        sellers = sorted((row for row in accepted if row["side"] == "sell"
                          and row["limit_price_minor"] <= clearing_price),
                         key=lambda row: (row["limit_price_minor"], row["sequence"]))
        for side in (buyers, sellers):
            remaining = volume
            for row in side:
                row["filled_quantity"] = min(remaining, row["accepted_quantity"])
                remaining -= row["filled_quantity"]
            if remaining:
                raise AssertionError("auction allocation did not exhaust its matched volume")
        trades, buyer_index, seller_index = [], 0, 0
        buy_remaining = [row["filled_quantity"] for row in buyers]
        sell_remaining = [row["filled_quantity"] for row in sellers]
        while buyer_index < len(buyers) and seller_index < len(sellers):
            if not buy_remaining[buyer_index]:
                buyer_index += 1
                continue
            if not sell_remaining[seller_index]:
                seller_index += 1
                continue
            quantity = min(buy_remaining[buyer_index], sell_remaining[seller_index])
            trades.append({"buyer": buyers[buyer_index]["owner"], "seller": sellers[seller_index]["owner"],
                           "buy_order_id": buyers[buyer_index]["order_id"],
                           "sell_order_id": sellers[seller_index]["order_id"],
                           "quantity": quantity, "price_minor": clearing_price})
            buy_remaining[buyer_index] -= quantity
            sell_remaining[seller_index] -= quantity
        for row in order_rows:
            quantity = row["filled_quantity"]
            notional = quantity * clearing_price
            fee = fee_minor(notional, self.fee_bps)
            row["fee_minor"] = fee
            row["unfilled_quantity"] = row["accepted_quantity"] - quantity
            if row["unfilled_quantity"]:
                row["reasons"].append("unmatched_day_order_expired")
            account = self.accounts[row["owner"]]
            if row["side"] == "buy":
                account.cash_minor -= notional + fee
                account.shares += quantity  # Newly bought units are not sellable this session.
            else:
                account.cash_minor += notional - fee
                account.shares -= quantity
                account.sellable_shares -= quantity
            self.fee_pool_minor += fee
            account.validate()
        if (sum(account.cash_minor for account in self.accounts.values()) + self.fee_pool_minor != self.initial_cash_minor
                or sum(account.shares for account in self.accounts.values()) != self.initial_shares
                or sum(trade["quantity"] for trade in trades) != volume):
            raise AssertionError("integer cash, shares or matched-volume ledger did not conserve")
        before = self.price_minor
        self.price_minor, self.session = clearing_price, session
        return {"pipeline_version": VERSION, "session": session, "execution_available": execution_available,
                "price_before_minor": before, "price_after_minor": clearing_price,
                "price_bounds_minor": [lower, upper], "matched_volume": volume,
                "clearing_imbalance": imbalance, "orders": order_rows, "trades": trades,
                "cash_total_minor": sum(account.cash_minor for account in self.accounts.values()),
                "shares_total": self.initial_shares, "fee_pool_minor": self.fee_pool_minor,
                "accounts": {name: asdict(account) for name, account in self.accounts.items()}}
