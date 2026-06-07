#!/usr/bin/env python3
"""Weekly data fetcher: 週乖離、融資餘額、外資投信週合計"""
import os, sys, json, io, datetime, time, requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOLDINGS = [{"code": "6274", "name": "台燿"}, {"code": "2327", "name": "國巨"}]
US_ETFS = ["SOXX", "QQQ"]
WEEKLY_MA = 20
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# ── 週乖離 ──────────────────────────────────────────

def get_yf_weekly(symbol, weeks=55):
    import yfinance as yf
    hist = yf.Ticker(symbol).history(period=f"{weeks}wk", interval="1wk")
    if hist.empty:
        return []
    return [{"close": round(float(r["Close"]), 2)} for _, r in hist.iterrows()]

def get_finmind_daily(code, start_date):
    r = requests.get("https://api.finmindtrade.com/api/v4/data", params={
        "dataset": "TaiwanStockPrice",
        "data_id": code,
        "start_date": start_date
    }, timeout=30)
    return r.json().get("data", [])

def daily_to_weekly(daily):
    weekly = {}
    for d in daily:
        dt = datetime.datetime.strptime(d["date"], "%Y-%m-%d")
        key = "%d-W%02d" % dt.isocalendar()[:2]
        weekly[key] = float(d["close"])
    return [{"close": v} for k, v in sorted(weekly.items())]

def calc_deviation(weekly):
    if len(weekly) < WEEKLY_MA:
        return None, None, None
    closes = [w["close"] for w in weekly]
    ma = round(sum(closes[-WEEKLY_MA:]) / WEEKLY_MA, 2)
    latest = closes[-1]
    dev = round((latest - ma) / ma * 100, 2)
    return round(latest, 2), ma, dev

# ── 融資餘額 ────────────────────────────────────────

def get_margin_balance():
    today = datetime.date.today()
    for i in range(7):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        url = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d.strftime('%Y%m%d')}&selectType=MS&response=json"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            data = r.json()
            if data.get('stat') == 'OK' and data.get('data'):
                # 最後一行是合計，取融資今日餘額（欄位 6）和維持率（欄位 10）
                row = data['data'][-1]
                balance = int(row[6].replace(',', ''))   # 融資今日餘額（千元）
                ratio   = row[10].replace(',', '') if len(row) > 10 else 'N/A'
                return {
                    "margin_balance_bil": round(balance / 100000, 0),  # 千元→億
                    "margin_ratio": ratio,
                    "data_date": d.strftime('%Y-%m-%d')
                }
        except Exception as e:
            print(f"  margin error {d}: {e}")
    return {}

# ── 外資／投信週合計 ─────────────────────────────────

def get_weekly_institutional(week_start, week_end):
    foreign_total = trust_total = days = 0
    cur = week_start
    while cur <= week_end:
        if cur.weekday() < 5:
            url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={cur.strftime('%Y%m%d')}&selectType=ALL&response=json"
            try:
                r = requests.get(url, headers=HEADERS, timeout=15)
                data = r.json()
                if data.get('stat') == 'OK':
                    for row in data.get('data', []):
                        name = row[1] if len(row) > 1 else ''
                        if any(x in name for x in ['ETF', 'ETN', '指數', '創新板']):
                            continue
                        try:
                            foreign_total += int(row[4].replace(',', '') or 0)
                            trust_total   += int(row[7].replace(',', '') or 0)
                        except:
                            pass
                    days += 1
                    print(f"  T86 {cur}: ok")
                time.sleep(0.4)
            except Exception as e:
                print(f"  T86 {cur}: {e}")
        cur += datetime.timedelta(days=1)
    return {
        "foreign_weekly_net": foreign_total,
        "trust_weekly_net":   trust_total,
        "trading_days":       days
    }

# ── Main ────────────────────────────────────────────

def main():
    today = datetime.date.today()
    # 若週六/日執行，往回抓週五
    wd = today.weekday()
    if wd == 5: today = today - datetime.timedelta(days=1)
    if wd == 6: today = today - datetime.timedelta(days=2)

    week_end   = today
    week_start = week_end - datetime.timedelta(days=4)
    start_52w  = (today - datetime.timedelta(weeks=55)).strftime('%Y-%m-%d')

    result = {
        "report_date": today.strftime('%Y-%m-%d'),
        "week_start":  week_start.strftime('%Y-%m-%d'),
        "week_end":    week_end.strftime('%Y-%m-%d'),
        "taiwan_index": {},
        "holdings": [],
        "us_etfs":  [],
        "chips":    {}
    }

    # TAIEX
    print("TAIEX 週乖離...")
    tw = get_yf_weekly("^TWII")
    c, ma, dev = calc_deviation(tw)
    if c:
        result["taiwan_index"] = {"code": "TAIEX", "close": c, "ma20w": ma, "deviation_pct": dev}
        print(f"  TAIEX {c}  MA20w {ma}  乖離 {dev}%")

    # 台股持股
    for s in HOLDINGS:
        print(f"{s['code']} 週乖離...")
        daily = get_finmind_daily(s["code"], start_52w)
        weekly = daily_to_weekly(daily)
        c, ma, dev = calc_deviation(weekly)
        if c:
            result["holdings"].append({
                "code": s["code"], "name": s["name"],
                "close": c, "ma20w": ma, "deviation_pct": dev
            })
            print(f"  {s['code']} {c}  乖離 {dev}%")

    # 美股 ETF
    for etf in US_ETFS:
        print(f"{etf} 週乖離...")
        weekly = get_yf_weekly(etf)
        c, ma, dev = calc_deviation(weekly)
        if c:
            result["us_etfs"].append({
                "code": etf, "close": c, "ma20w": ma, "deviation_pct": dev
            })
            print(f"  {etf} {c}  乖離 {dev}%")

    # 融資餘額
    print("融資餘額...")
    result["chips"].update(get_margin_balance())

    # 外資／投信週合計
    print("外資投信週合計...")
    result["chips"].update(get_weekly_institutional(week_start, week_end))

    # 儲存
    os.makedirs("data", exist_ok=True)
    with open("data/weekly_data.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n=== 完成 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
