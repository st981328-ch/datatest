#!/usr/bin/env python3
"""Fetch FinMind stock data and save to data/stock_data.json."""
import json
import os
import requests
from datetime import datetime, timedelta, timezone

HOLDINGS = [
    {"code": "6274", "name": "台燿科技", "sector": "CCL"},
    {"code": "2327", "name": "國巨", "sector": "被動元件"},
]
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def prev_trading_date():
    tw_tz = timezone(timedelta(hours=8))
    now_tw = datetime.now(tw_tz)
    d = now_tw.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d"), now_tw.strftime("%Y-%m-%d")


def finmind_price(code, date):
    r = requests.get(FINMIND_URL, params={
        "dataset": "TaiwanStockPrice", "data_id": code,
        "start_date": date, "end_date": date,
    }, timeout=15)
    data = r.json().get("data", [])
    return data[0] if data else None


def finmind_inst(code, date):
    r = requests.get(FINMIND_URL, params={
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
        "data_id": code, "start_date": date, "end_date": date,
    }, timeout=15)
    rows = r.json().get("data", [])
    totals = {}
    for row in rows:
        k = row["name"]
        totals[k] = totals.get(k, 0) + row["buy"] - row["sell"]

    def lots(k): return round(totals.get(k, 0) / 1000, 1)
    def s(v): return f"+{v}" if v >= 0 else str(v)

    foreign = lots("Foreign_Investor")
    trust = lots("Investment_Trust")
    dealer = round(
        (totals.get("Dealer_self", 0) + totals.get("Dealer_Hedging", 0)) / 1000, 1
    )
    total = round(foreign + trust + dealer, 1)
    return {
        "foreign": foreign, "trust": trust, "dealer": dealer, "total": total,
        "text": f"外資{s(foreign)}張，投信{s(trust)}張，自營{s(dealer)}張，合計{s(total)}張",
    }


def main():
    prev_date, report_date = prev_trading_date()
    result = {
        "report_date": report_date,
        "prev_date": prev_date,
        "stocks": []
    }

    for stock in HOLDINGS:
        p = finmind_price(stock["code"], prev_date)
        inst = finmind_inst(stock["code"], prev_date)

        if not p:
            result["stocks"].append({
                "code": stock["code"],
                "name": stock["name"],
                "sector": stock["sector"],
                "error": "no_data"
            })
            print(f"No data for {stock['name']} on {prev_date}")
            continue

        spread = p["spread"]
        close = p["close"]
        pct = round(spread / (close - spread) * 100, 2) if (close - spread) else 0
        s = lambda v: f"+{v}" if v >= 0 else str(v)

        result["stocks"].append({
            "code": stock["code"],
            "name": stock["name"],
            "sector": stock["sector"],
            "close": close,
            "spread": spread,
            "spread_pct": s(pct) + "%",
            "foreign": inst["foreign"],
            "trust": inst["trust"],
            "dealer": inst["dealer"],
            "total": inst["total"],
            "inst_text": inst["text"],
        })
        print(f"{stock['name']} {close} {s(spread)} | {inst['text']}")

    os.makedirs("data", exist_ok=True)
    with open("data/stock_data.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Saved data/stock_data.json for prev_date={prev_date}")


if __name__ == "__main__":
    main()
