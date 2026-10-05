"""Independent rational quote arithmetic and direct candidate-price enumeration."""
from collections import Counter
from fractions import Fraction
import math
import statistics

from .audit_public_information_risk import expected_receipt
from .signal_transmission_diagnostics import STAGES


def check(value, message):
    if not value:
        raise ValueError(message)


def rational_quote(price, side, base, shift, spread, tick, bounds):
    value = Fraction(price, tick) * (1 + Fraction(str(base))/10000 + Fraction(str(shift))/10000
        + (-1 if side == "buy" else 1)*Fraction(spread,20000))
    integer = value.numerator//value.denominator if side == "buy" else -((-value.numerator)//value.denominator)
    return max(bounds[0],min(bounds[1],integer*tick))


def direct_price(call, quotes, tick):
    prior=call["price_before_minor"]
    lower,upper=call["price_bounds_minor"]
    candidates={lower,upper,prior}
    active=[(o["side"],o["accepted_quantity"],quotes[o["owner"]]) for o in call["orders"] if o["accepted_quantity"]]
    for _,_,price in active:
        candidates.update(p for p in (price-tick,price,price+tick) if lower<=p<=upper)
    scored=[]
    for p in candidates:
        buys=sum(q for side,q,limit in active if side=="buy" and limit>=p)
        sells=sum(q for side,q,limit in active if side=="sell" and limit<=p)
        scored.append((-min(buys,sells),abs(buys-sells),abs(p-prior),p,buys-sells))
    best=min(scored)
    if best[0]==0:
        buys=sum(q for side,q,limit in active if side=="buy" and limit>=prior)
        sells=sum(q for side,q,limit in active if side=="sell" and limit<=prior)
        return prior,0,buys-sells
    return best[3],-best[0],best[4]


def independent_diagnose(day, stock, specs, row, parameters, venue, background, previous):
    call=day["portfolio_auction"]["asset_calls"][stock]
    price,bounds=call["price_before_minor"],call["price_bounds_minor"]
    owners=sorted(n for n,s in specs.items() if s["kind"]=="strategy" or s["asset"]==stock)
    orders={o["owner"]:o for o in call["orders"]}
    check(len(owners)==24 and len(orders)==len(call["orders"]),"independent owner scope differs")
    counters=Counter()
    roles={role:Counter() for role in ("aggressive","conservative","institutional","background")}
    stage_values={stage:[] for stage in STAGES}
    no_market,no_issuer={},{}
    for owner in owners:
        spec=specs[owner]
        role=spec["parameters"]["role"] if spec["kind"]=="strategy" else "background"
        r=day["issuer_information_receipts"][owner][stock]
        check(r==expected_receipt(row,parameters,stock,owner),"independent received message differs")
        public=r.get("public_information_receipt")
        private_seen=public["private_received"] if public else r["received"]
        residual=row["risk_budget"]["residual_raw_shift_bps"] if private_seen else 0.0
        market=(row["risk_budget"]["market_raw_shift_bps"]*row["public_information_budget"]["public_market_multiplier"]
            if public else row["risk_budget"]["market_raw_shift_bps"] if private_seen else 0.0)
        without_market=min(parameters["max_shift_bps"],max(-parameters["max_shift_bps"],residual))
        applied=r["applied_valuation_shift_bps"]
        stage_values["mean_received_market_before_cap_bps"].append(market/24)
        stage_values["mean_received_total_after_cap_bps"].append(applied/24)
        local={"owner_positions":1,"private_received":int(private_seen),"nonzero_market_component_received":int(market!=0),
            "nonzero_applied_total_received":int(applied!=0),"received_total_capped":int(residual+market!=applied)}
        order=orders.get(owner)
        if role!="background":
            d=day["decisions"][owner]
            delta=d["order_weight_changes"][stock]
            unrounded=abs(delta)*d["nav_minor"]/(price*venue["lot_size"])
            n=int(unrounded)*venue["lot_size"]
            if delta<0:
                n=min(n,previous[owner]["shares"][stock])
            check(n==(order["quantity"] if order else 0),"independent whole-lot target differs")
            local.update(strategy_positions=1,strategy_zero_delta=int(delta==0),strategy_nonzero_delta_no_order=int(delta!=0 and n==0),
                strategy_sub_lot_delta=int(0<unrounded<1),strategy_wait_positions=int("confirmation_or_rebalance_wait" in d["reasons"]),
                strategy_minimum_trade_positions=int("minimum_portfolio_trade" in d["reasons"]),strategy_risk_liquidation_positions=int(d["risk_liquidation"]))
            stage_values["mean_strategy_belief_effect_bps"].append(d["issuer_belief_contributions"][stock]*parameters["belief_scale_bps"]/12)
            if order:
                base=Fraction(str(d.get("quote_base_beliefs",d["issuer_base_beliefs"])[stock]))*venue["quote_response_bps"]
                expected=rational_quote(price,order["side"],base,d["issuer_quote_shift_bps"][stock],venue["quote_spread_bps"],venue["tick_minor"],bounds)
                m=rational_quote(price,order["side"],base,spec["parameters"]["market_sensitivity"]*without_market,venue["quote_spread_bps"],venue["tick_minor"],bounds)
                z=rational_quote(price,order["side"],base,0,venue["quote_spread_bps"],venue["tick_minor"],bounds)
        else:
            demand=day["background_demands"][stock][owner]
            check(demand["requested_quantity"]==(order["quantity"] if order else 0),"independent background demand differs")
            local["background_positions"]=1
            if order:
                base_quote=demand["issuer_valuation_response"]["base_limit_price_minor"]
                inactive=not r["received"] or (public and not private_seen and market==0)
                expected=base_quote if inactive else rational_quote(price,order["side"],0,applied,-2*background["urgency_bps"],venue["tick_minor"],bounds)
                m=(rational_quote(price,order["side"],0,without_market,-2*background["urgency_bps"],venue["tick_minor"],bounds)
                    if private_seen else base_quote)
                z=base_quote
        if order:
            check(expected==order["limit_price_minor"],"independent exact rational physical quote differs")
            no_market[owner],no_issuer[owner]=m,z
            effect=(expected-m)*10000/price
            stage_values["mean_emitted_market_quote_effect_bps"].append(effect/24)
            stage_values["mean_accepted_market_quote_effect_bps"].append(effect*order["accepted_quantity"]/order["quantity"]/24)
            stage_values["mean_filled_market_quote_effect_bps"].append(effect*order["filled_quantity"]/order["quantity"]/24)
            stage_values["mean_emitted_total_quote_effect_bps"].append((expected-z)*10000/price/24)
            local.update(submitted_orders=1,submitted_orders_market_quote_changed=int(expected!=m),submitted_orders_total_quote_changed=int(expected!=z),
                accepted_orders=int(order["accepted_quantity"]>0),filled_orders=int(order["filled_quantity"]>0))
            for prefix,field in (("requested","quantity"),("accepted","accepted_quantity"),("filled","filled_quantity")):
                local[prefix+"_"+order["side"]]=order[field]
            local["cash_clipped_quantity"]=(order["quantity"]-order["accepted_quantity"]) if "cash_and_fee_reservation" in order["reasons"] else 0
            local["inventory_clipped_quantity"]=(order["quantity"]-order["accepted_quantity"]) if "sellable_inventory" in order["reasons"] else 0
        counters.update(local)
        roles[role].update(local)
    factual=direct_price(call,{o["owner"]:o["limit_price_minor"] for o in call["orders"]},venue["tick_minor"])
    check(factual==(call["price_after_minor"],call["matched_volume"],call["clearing_imbalance"]),"independent factual accepted book differs")
    probes={}
    for label,quotes in (("remove_market_quote_component",no_market),("remove_all_issuer_quote_component",no_issuer)):
        result=direct_price(call,quotes,venue["tick_minor"])
        probes[label]=dict(zip(("price_minor","matched_volume","imbalance"),result))
        counters[label+"_same_price"]+=int(result[0]==factual[0])
        counters[label+"_same_volume"]+=int(result[1]==factual[1])
    counters.update(books=1,traded_flat_books=int(call["matched_volume"]>0 and call["price_after_minor"]==price),no_trade_books=int(call["matched_volume"]==0))
    values={key:math.fsum(items) for key,items in stage_values.items()}
    values["generated_market_before_cap_bps"]=row["risk_budget"]["market_raw_shift_bps"]
    values["actual_return_bps"]=(call["price_after_minor"]/price-1)*10000
    return {"stages":values,"counts":dict(counters),"roles":{k:dict(v) for k,v in roles.items()},"accepted_book_probes":probes}


def independent_statistics(rows,mask):
    panel={(r["stock_code"],r["trade_date"]):r for r in rows}
    check(len(panel)==len(rows)==len(mask["stocks"])*len(mask["dates"]),"independent stage panel scope differs")
    pairs=[p for p in mask["correlation_pairs"] if p["primary_eligible"]]
    result={}
    for stage in STAGES:
        values=[r["stages"][stage] for r in rows]
        mean=math.fsum(values)/len(values)
        std=math.sqrt(math.fsum((v-mean)**2 for v in values)/len(values))
        correlations,undefined=[],[]
        for p in pairs:
            left=[panel[p["left"],d]["stages"][stage] for d in p["known_dates"]]
            right=[panel[p["right"],d]["stages"][stage] for d in p["known_dates"]]
            try:
                value=statistics.correlation(left,right)
            except statistics.StatisticsError:
                value=None
            if value is None:
                undefined.append([p["left"],p["right"]])
            else:
                correlations.append(value)
        common=[math.fsum(panel[s,d]["stages"][stage] for s in mask["stocks"])/len(mask["stocks"])
            for d in mask["full_cohort_portfolio_common_dates"]]
        centre=math.fsum(common)/len(common)
        result[stage]={"pooled_mean_bps":mean,"pooled_std_bps":std,"zero_fraction":sum(v==0 for v in values)/len(values),
            "mean_stock_correlation":math.fsum(correlations)/len(correlations) if len(correlations)==len(pairs) and pairs else None,
            "fixed_primary_pairs":len(pairs),"defined_primary_pairs":len(correlations),"undefined_primary_pairs":undefined,
            "common_sessions":len(common),"equal_weight_common_std_bps":math.sqrt(math.fsum((v-centre)**2 for v in common)/len(common)) if len(common)>=2 else None}
    return result
