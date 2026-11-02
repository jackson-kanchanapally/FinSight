"""Explain reported gold drivers in plain language without inventing a forecast."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re

from app.services.gold_sources import headline_impact


@dataclass(frozen=True)
class DriverRule:
    key: str
    label: str
    pattern: str
    impact: str
    reason: str
    outlook: str
    scope: str = "GLOBAL"


RULES = (
    DriverRule("dollar_up", "A stronger US dollar",
        r"strong(?:er)? (?:us )?dollar|firm(?:er)? (?:us )?dollar|dollar.{0,18}(?:strengthens?|gains?|climbs?|rises?|firms?)",
        "NEGATIVE", "A stronger dollar makes dollar-priced gold more expensive for buyers using other currencies. That can reduce demand and put pressure on gold.",
        "If the dollar stays strong, gold may struggle to rise. A weaker dollar would reduce that pressure."),
    DriverRule("dollar_down", "A weaker US dollar",
        r"weak(?:er)? (?:us )?dollar|soft(?:er)? (?:us )?dollar|dollar.{0,18}(?:weakens?|falls?|slides?|drops?|retreats?)",
        "POSITIVE", "A weaker dollar makes dollar-priced gold cheaper for buyers using other currencies. That can support demand for gold.",
        "Gold could keep some support if the dollar stays weak. A dollar rebound could reverse that help."),
    DriverRule("rates_up", "Expectations of higher US interest rates",
        r"fed.{0,25}(?:hawks?|hawkish|rate hikes?)|hawkish.{0,15}(?:fed|policy)|higher.for.longer|fewer.{0,12}rate.cuts?|rate.cut.{0,25}(?:fade|recede|diminish)|higher (?:us )?interest rates",
        "NEGATIVE", "Gold pays no interest. When investors expect US rates to stay high, interest-paying savings and bonds can look more attractive than gold.",
        "Gold may remain under pressure if higher-rate expectations persist. Softer Fed comments or weaker economic data could ease that pressure."),
    DriverRule("rates_down", "Expectations of lower US interest rates",
        r"fed.{0,20}(?:cuts? rates|eases? policy)|(?:hopes?|bets?|expectations?).{0,25}(?:rate cuts?|lower rates)|lower (?:us )?interest rates|dovish.{0,15}(?:fed|policy)",
        "POSITIVE", "Lower expected US rates can make interest-paying assets less attractive relative to gold. That can help gold, which pays no interest.",
        "Gold could benefit if lower-rate expectations hold. Strong economic data or firmer Fed comments could weaken that support."),
    DriverRule("yields_up", "Higher bond yields",
        r"higher (?:us |treasury |bond |real )*yields|(?:treasury |bond |real )?yields.{0,18}(?:rise|climb|jump|gain|surge)",
        "NEGATIVE", "Higher yields mean bonds pay investors more. This can make gold less attractive, although inflation and other forces can offset the effect.",
        "Gold could stay under pressure if yields remain high. Falling yields would reduce competition from bonds."),
    DriverRule("yields_down", "Lower bond yields",
        r"lower (?:us |treasury |bond |real )*yields|(?:treasury |bond |real )?yields.{0,18}(?:fall|drop|slide|decline|retreat)",
        "POSITIVE", "Lower yields reduce the income investors give up by holding gold instead of bonds. This can support gold demand.",
        "Lower yields could support gold if they persist. A rebound in yields could remove some of that support."),
    DriverRule("profit_taking", "Selling to lock in earlier gains", r"profit.taking|booking profits|profit booking",
        "NEGATIVE", "Some investors are selling after earlier gains. That can pull prices down for a time, without proving that the longer-term outlook has weakened.",
        "Watch whether selling continues or new buyers step in. One round of profit-taking does not establish a lasting downtrend."),
    DriverRule("haven_buying", "Demand for safety",
        r"(?:rising|strong|growing|increased) safe.haven demand|safe.haven (?:buying|inflows)|haven demand.{0,12}(?:rises|grows|surges)|escalating (?:war|conflict|tensions)|geopolitical tensions (?:rise|escalat)|ceasefire.{0,12}(?:collapses|breaks down)",
        "POSITIVE", "During uncertainty, some investors buy gold to preserve value. That can support prices, but dollar and rate changes may still outweigh it.",
        "Continuing uncertainty could support demand for gold. Easing tensions could reduce that demand."),
    DriverRule("haven_easing", "Easing geopolitical tensions", r"ceasefire (?:agreed|deal|agreement|holds|announced|takes effect)|easing (?:geopolitical )?tensions|de.escalation",
        "NEGATIVE", "Easing tensions may reduce the need to hold gold for safety. The price effect also depends on the dollar, rates and other demand.",
        "A lasting reduction in tensions could weaken safety-related buying. Renewed uncertainty could bring it back."),
    DriverRule("central_buying", "Central-bank gold purchases",
        r"central.banks?.{0,25}(?:buying|buys|purchases|adds? gold)|central.bank (?:buying|purchases)",
        "POSITIVE", "Reported central-bank purchases add demand for gold. This can support prices, but does not guarantee a rise on its own.",
        "Watch whether the reported buying continues. A single purchase report is not enough to project a price target."),
    DriverRule("inflation", "New inflation information", r"\bcpi\b|inflation (?:data|report|rises|falls|cools|accelerat)|hotter.{0,12}inflation",
        "UNCLEAR", "Inflation can help gold demand as a store of value. But if it keeps interest rates high, it can also hurt gold. Inflation alone does not give a clear direction.",
        "Watch how interest-rate expectations and the dollar react to the inflation news. Their response matters more than the inflation headline alone."),
    DriverRule("fed_update", "A Federal Reserve policy update", r"\bfed\b|\bfomc\b|federal reserve",
        "UNCLEAR", "A Fed update can change expectations for US interest rates. The headline alone does not tell us whether the actual decision was better or worse for gold.",
        "Check the Fed decision and the dollar or bond-market reaction before assuming gold will rise or fall."),
    DriverRule("rupee_down", "A weaker Indian rupee", r"weak(?:er)? rupee|rupee.{0,18}(?:weakens?|falls?|slides?|depreciat)",
        "POSITIVE", "A weaker rupee can make imported gold more expensive in INR, even if the dollar gold price is unchanged.",
        "Watch the rupee as well as the global gold quote when judging Indian prices.", "INDIA"),
    DriverRule("rupee_up", "A stronger Indian rupee", r"strong(?:er)? rupee|rupee.{0,18}(?:strengthens?|gains?|appreciat)",
        "NEGATIVE", "A stronger rupee can reduce the INR cost of imported gold, even if the dollar gold price is unchanged.",
        "Watch whether rupee strength continues before expecting the same change in local retail rates.", "INDIA"),
)


def reported_match(pattern, text):
    for match in re.finditer(pattern, text, re.IGNORECASE):
        prefix = text[max(0, match.start() - 55):match.start()]
        if re.search(r"\b(?:if|unless)\b[^.!?;]*$", prefix, re.IGNORECASE):
            continue
        if re.search(r"\b(?:not|never|without|no longer|no|unlikely to|fails? to|little|limited|fading|lacking)\s+(?:\w+\s+){0,3}$", prefix, re.IGNORECASE):
            continue
        if re.search(r"\b(?:not|never|could|may|might|would)\b", match.group(), re.IGNORECASE):
            continue
        if re.match(r"\W+(?:would|could|may|might)\b", text[match.end():], re.IGNORECASE):
            continue
        if re.match(r"\W+(?:fade[sd]?|wanes?|slows?|slower|collapses?|retreats?)\b", text[match.end():], re.IGNORECASE):
            continue
        return True
    return False


def explain_headline(row):
    title = row.title
    summary = row.summary or ""
    evidence = title + ". " + summary
    found = [rule for rule in RULES if reported_match(rule.pattern, evidence)]
    if any(rule.key in {"rates_up", "rates_down"} for rule in found):
        found = [rule for rule in found if rule.key != "fed_update"]
    observed, _ = headline_impact(title, row.source)
    if found:
        directions = {rule.impact for rule in found} - {"UNCLEAR"}
        if observed != "UNCLEAR" and not re.search(r"\bindia\b|hyderabad|rupee", title, re.IGNORECASE):
            directions.add(observed)
        impact = "MIXED" if "MIXED" in directions or {"POSITIVE", "NEGATIVE"} <= directions else next(iter(directions), "UNCLEAR")
        return {"impact": impact, "reason": found[0].reason, "observed": observed, "rules": found}
    reason = {"POSITIVE": "The report says gold rose, but it does not identify a clear reason for the rise.",
              "NEGATIVE": "The report says gold fell, but it does not identify a clear reason for the fall.",
              "MIXED": "The report describes moves in both directions. It does not establish a single trend.",
              "UNCLEAR": "This report does not provide enough evidence to say whether gold should rise or fall."}[observed]
    return {"impact": observed, "reason": reason, "observed": observed, "rules": []}


def exchange_rate_context(global_quote):
    if not global_quote or not global_quote.get("usd_inr_rate"):
        return None
    rate = Decimal(global_quote["usd_inr_rate"])
    prefix = f"USD/INR is ₹{rate:.4f} per $1."
    if global_quote.get("usd_inr_freshness") == "STALE":
        return prefix + " This observation is old, so it cannot establish the current rupee direction or explain today's retail gold rate."
    change = global_quote.get("usd_inr_change_pct")
    if change is None:
        return prefix + " There is no earlier stored exchange rate to compare, so the rupee direction is not established yet."
    move = Decimal(change)
    if move > 0:
        return (prefix + f" It rose {move:.2f}% from the previous stored rate, meaning the rupee weakened against the dollar. "
                "That can raise the INR cost of imported gold if other factors stay equal; its contribution to the published retail rate is not measured.")
    if move < 0:
        return (prefix + f" It fell {abs(move):.2f}% from the previous stored rate, meaning the rupee strengthened against the dollar. "
                "That can ease the INR cost of imported gold if other factors stay equal; its contribution to the published retail rate is not measured.")
    return (prefix + " It is little changed from the previous stored rate, so this comparison shows no clear currency push on Indian gold. "
            "Other local pricing factors remain unmeasured.")


def local_context(quote, label, local_drivers, fx_context):
    if quote is None:
        first = f"No {label} reference rate has been verified yet. A Hyderabad quote cannot stand in for an India-wide quote, or vice versa."
    else:
        when = date.fromisoformat(quote["rate_date"]).strftime("%d %b")
        if quote["freshness"] == "STALE":
            first = f"The {label} rate is dated {when} and is old. It does not confirm today's retail price."
        else:
            value = quote.get("change_24k_pct")
            if value is None:
                first = f"The latest verified {label} reference is dated {when}."
            else:
                change = Decimal(value)
                move = f"up {abs(change):.2f}%" if change > 0 else f"down {abs(change):.2f}%" if change < 0 else "unchanged"
                first = f"The {label} 24K reference is {move} versus its previous day, as published for {when}."
    extra = fx_context or (local_drivers[0]["reason"] if local_drivers else
        "INR rates also depend on the rupee and local pricing; this check has not measured their separate effects.")
    return first + " " + extra


def build_analysis(prices, headlines):
    drivers, seen = [], set()
    observed = set()
    for row in headlines:
        reading = explain_headline(row)
        if not re.search(r"\bindia\b|hyderabad|rupee", row.title, re.IGNORECASE) and reading["observed"] != "UNCLEAR":
            observed.add(reading["observed"])
        for rule in reading["rules"]:
            if rule.key in seen:
                continue
            seen.add(rule.key)
            drivers.append({"headline_id": row.id, "title": row.title, "source": row.source,
                "url": row.url, "impact": rule.impact, "reason": rule.reason,
                "factor": rule.label, "scope": rule.scope, "what_next": rule.outlook})
    global_drivers = [item for item in drivers if item["scope"] == "GLOBAL"]
    local_drivers = [item for item in drivers if item["scope"] == "INDIA"]
    directions = {item["impact"] for item in global_drivers} - {"UNCLEAR"}
    signals = directions | observed
    bias = "MIXED" if "MIXED" in signals or {"POSITIVE", "NEGATIVE"} <= signals else next(iter(signals), "UNCLEAR")
    primary = next((item for item in global_drivers if item["impact"] == bias), global_drivers[0] if global_drivers else None)
    if not headlines:
        summary = "No recent gold news was verified. There is not enough news evidence for a directional outlook."
    elif bias == "MIXED":
        summary = "The reports contain conflicting price moves or drivers. There is no clear one-way outlook from this evidence."
    elif global_drivers and bias in {"POSITIVE", "NEGATIVE"}:
        direction = "support for" if bias == "POSITIVE" else "pressure on"
        factor = primary["factor"][0].lower() + primary["factor"][1:]
        summary = f"Recent reports point to {direction} gold. One reported factor is {factor}."
    elif bias == "NEGATIVE":
        summary = "Reports say gold fell, but the available details do not establish why. That alone does not prove the fall will continue."
    elif bias == "POSITIVE":
        summary = "Reports say gold rose, but the available details do not establish why. That alone does not prove the rise will continue."
    else:
        summary = "There is fresh news, but its effect on gold is not yet clear from the available details."
    outlook = primary["what_next"] if primary else "Wait for a fresh, verified driver before expecting the latest move to continue. The next dollar, rates or demand update may change the picture."
    if not any(quote and quote["freshness"] != "STALE" for quote in prices.values()):
        outlook = "The price quotes are missing or old. A fresh price is needed before judging the next move."
    visible_drivers = (global_drivers + local_drivers)[:3]
    if not visible_drivers:
        for row in headlines[:2]:
            reading = explain_headline(row)
            visible_drivers.append({"headline_id": row.id, "title": row.title, "source": row.source,
                "url": row.url, "impact": reading["impact"], "reason": reading["reason"],
                "factor": "Reported price move", "scope": "GLOBAL"})
    fx_context = exchange_rate_context(prices.get("global"))
    return {"bias": bias, "summary": summary, "what_next": outlook, "drivers": visible_drivers,
            "fx_context": fx_context,
            "local_context": {key: local_context(prices.get(key), label, local_drivers, fx_context)
                              for key, label in (("india", "India"), ("hyderabad", "Hyderabad"))},
            "comparison_note": "The global percentage compares stored quotes; retail percentages compare the previous published day. These periods differ.",
            "method": "Based on dated news headlines and their short published summaries. The explanations describe possible effects; other market forces may outweigh them."}
