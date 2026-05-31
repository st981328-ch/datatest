#!/usr/bin/env python3
"""Fetch stock data: holdings from FinMind, momentum sector from TWSE T86."""
import json
import os
import requests
from datetime import datetime, timedelta, timezone

HOLDINGS = [
    {"code": "6274", "name": "台燿科技"},
    {"code": "2327", "name": "國巨"},
]
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
EXCLUDE_SECTORS = {"ETF", "ETN", "Index", "創新板股票"}


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


def fetch_sector_map():
    """Get stock_id -> industry_category from FinMind (TWSE stocks only)."""
    r = requests.get(FINMIND_URL, params={"dataset": "TaiwanStockInfo"}, timeout=30)
    return {
        s["stock_id"]: s["industry_category"]
        for s in r.json().get("data", [])
        if s.get("type") == "twse" and s.get("industry_category")
    }


def fetch_twse_t86(date):
    """Fetch TWSE T86 historical institutional data for all stocks."""
    date_twse = date.replace("-", "")
    r = requests.get(TWSE_T86_URL, params={
        "date": date_twse, "selectType": "ALL", "response": "json"
    }, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    d = r.json()
    if d.get("stat") != "OK":
        return None, None
    fields = d.get("fields", [])
    # Find column indices
    idx = {f: i for i, f in enumerate(fields)}
    return d.get("data", []), idx


def parse_num(s):
    try:
        return int(str(s).replace(",", "").replace(" ", ""))
    except:
        return 0


def build_momentum(prev_date):
    """Find top sector and top 3 stocks by institutional net buying."""
    sector_map = fetch_sector_map()
    print(f"  Sector map: {len(sector_map)} TWSE stocks")

    rows, idx = fetch_twse_t86(prev_date)
    if not rows:
        print("  TWSE T86: no data (holiday?)")
        return None, []
    print(f"  TWSE T86: {len(rows)} rows")

    # Column indices
    foreign_idx = idx.get("外陸資買賣超股數(不含外資自營商)", 4)
    fself_idx   = idx.get("外資自營商買賣超股數", 7)
    trust_idx   = idx.get("投信買賣超股數", 10)
    dealer_idx  = idx.get("自營商買賣超股數", 11)
    total_idx   = idx.get("三大法人買賣超股數", 18)

    sector_net = {}
    stock_data = {}

    for row in rows:
        if len(row) <= total_idx:
            continue
        code = row[0].strip()
        total = parse_num(row[total_idx])
        foreign = parse_num(row[foreign_idx]) + parse_num(row[fself_idx])
        trust = parse_num(row[trust_idx])
        dealer = parse_num(row[dealer_idx])
        stock_data[code] = {
            "total": total, "foreign": foreign,
            "trust": trust, "dealer": dealer
        }
        sector = sector_map.get(code)
        if sector and sector not in EXCLUDE_SECTORS:
            sector_net[sector] = sector_net.get(sector, 0) + total

    if not sector_net:
        return None, []

    top_sector = max(sector_net, key=sector_net.get)
    top_sector_net = sector_net[top_sector]
    print(f"  Top sector: {top_sector} ({top_sector_net:,} shares)")

    # Top 3 stocks in that sector
    sector_stocks = [
        (code, d) for code, d in stock_data.items()
        if sector_map.get(code) == top_sector
    ]
    top3 = sorted(sector_stocks, key=lambda x: x[1]["total"], reverse=True)[:3]

    def s(v): return f"+{v}" if v >= 0 else str(v)

    momentum = []
    for code, inst in top3:
        p = finmind_price(code, prev_date)
        foreign_lots = round(inst["foreign"] / 1000, 1)
        trust_lots   = round(inst["trust"] / 1000, 1)
        dealer_lots  = round(inst["dealer"] / 1000, 1)
        total_lots   = round(inst["total"] / 1000, 1)
        inst_text = f"外資{s(foreign_lots)}張，投信{s(trust_lots)}張，自營{s(dealer_lots)}張，合計{s(total_lots)}張"

        entry = {
            "code": code,
            "foreign": foreign_lots,
            "trust": trust_lots,
            "dealer": dealer_lots,
            "total": total_lots,
            "inst_text": inst_text,
        }
        if p:
            spread = p["spread"]
            close = p["close"]
            pct = round(spread / (close - spread) * 100, 2) if (close - spread) else 0
            entry.update({
                "close": close,
                "spread": spread,
                "spread_pct": s(pct) + "%",
            })
            print(f"  Momentum {code}: {close} {s(spread)} | {inst_text}")
        else:
            print(f"  Momentum {code}: no price data")

        momentum.append(entry)

    return {"name": top_sector, "net_buy_shares": top_sector_net}, momentum


def main():
    prev_date, report_date = prev_trading_date()
    result = {
        "report_date": report_date,
        "prev_date": prev_date,
        "stocks": [],
        "top_sector": None,
        "momentum": [],
    }

    print(f"=== Holdings ({prev_date}) ===")
    for stock in HOLDINGS:
        p = finmind_price(stock["code"], prev_date)
        inst = finmind_inst(stock["code"], prev_date)

        if not p:
            result["stocks"].append({"code": stock["code"], "error": "no_data"})
            print(f"  No data for {stock['name']}")
            continue

        spread = p["spread"]
        close = p["close"]
        pct = round(spread / (close - spread) * 100, 2) if (close - spread) else 0
        s = lambda v: f"+{v}" if v >= 0 else str(v)

        result["stocks"].append({
            "code": stock["code"],
            "close": close,
            "spread": spread,
            "spread_pct": s(pct) + "%",
            "foreign": inst["foreign"],
            "trust": inst["trust"],
            "dealer": inst["dealer"],
            "total": inst["total"],
            "inst_text": inst["text"],
        })
        print(f"  {stock['name']} {close} {s(spread)} | {inst['text']}")

    print(f"\n=== Momentum ({prev_date}) ===")
    top_sector, momentum = build_momentum(prev_date)
    result["top_sector"] = top_sector
    result["momentum"] = momentum

    os.makedirs("data", exist_ok=True)
    with open("data/stock_data.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nSaved data/stock_data.json")


if __name__ == "__main__":
    main()
