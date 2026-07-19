from __future__ import annotations

from decimal import Decimal

from trading.domain.product import OptionRight, OptionSpec, option_multiplier


def maximum_expiry_loss(legs: tuple[tuple[OptionSpec,int],...],entry_credit: Decimal,quantity: int=1) -> Decimal:
    if not legs:return Decimal("0")
    expiries={spec.expiry for spec,_ in legs};multipliers={option_multiplier(spec) for spec,_ in legs}
    if len(expiries)!=1 or len(multipliers)!=1:raise ValueError("option risk requires a common expiry and multiplier")
    strikes=sorted({spec.strike for spec,_ in legs});high=(strikes[-1]*Decimal("2")) if strikes else Decimal("1")
    spots=(Decimal("0"),*strikes,high);multiplier=next(iter(multipliers))
    pnl=[]
    for spot in spots:
        payoff=Decimal("0")
        for spec,sign in legs:
            intrinsic=max(Decimal("0"),spot-spec.strike) if spec.right is OptionRight.CALL else max(Decimal("0"),spec.strike-spot)
            payoff+=Decimal(sign)*intrinsic
        pnl.append((entry_credit+payoff)*multiplier*quantity)
    return max(Decimal("0"),-min(pnl))
