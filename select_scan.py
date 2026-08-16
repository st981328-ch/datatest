#!/usr/bin/env python3
"""股癌選股腦 — Phase 1：族群 gate。

先判斷盤面有沒有「族群」在成形(股癌+投資癮的最高閘門):用 TWSE T86 全市場法人買賣超
+ FinMind 類股對應,找出「淨買超大 且 買超家數多(廣度)」的族群。沒成團 → 盤面難做、觀望。
只打 2 個 API(TWSE T86 + FinMind 類股表),不吃 FinMind 逐檔限流。
"""
import sys, io, json, os
from datetime import datetime, timedelta
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_data as fd

PRICE_TOPN = 15       # 只對法人買超前 N 名加價格確認(省 API)


def _price_signal(code, prev):
    """近一年日K→表態/創新高確認(股癌:等價格表態才進)。"""
    start = (datetime.strptime(prev, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
    rows = fd._finmind_rows("TaiwanStockPrice", code, start, prev)
    if len(rows) < 60:
        return {}
    rows.sort(key=lambda x: x["date"])
    c = [x["close"] for x in rows]; h = [x["max"] for x in rows]
    px, n = c[-1], len(rows)
    hi52 = max(h); ma60 = sum(c[-60:]) / 60
    return {
        "price": px,
        "near_high_pct": round((hi52 - px) / hi52 * 100, 1),   # 距52週高% (0=剛創新高)
        "new52": px >= hi52 * 0.995,                            # 創(近)52週新高
        "new20": px >= max(c[-20:]),                            # 創20日新高=突破表態
        "above_ma60": px >= ma60,
        "chg5": round((px / c[-6] - 1) * 100, 1) if n >= 6 else None,
        "chg20": round((px / c[-21] - 1) * 100, 1) if n >= 21 else None,
    }

MIN_STOCKS = 5        # 一個族群至少要幾檔才算數(避免小類股雜訊)
BROAD = 0.55          # 買超家數佔比 ≥ 此值才算「廣度夠、成團」
GROUP_MIN_LOTS = 3000 # 族群淨買超至少幾張才算強


def _info_maps():
    """一次抓 TaiwanStockInfo,同時建 類股表 + 中文名表(省一個 call)。"""
    r = fd._get(fd.FINMIND_URL, params={"dataset": "TaiwanStockInfo"}, timeout=30)
    sec, nm = {}, {}
    for s in fd._safe_json(r).get("data", []):
        sid = s.get("stock_id")
        if not sid:
            continue
        if s.get("type") == "twse" and s.get("industry_category"):
            sec.setdefault(sid, s["industry_category"])
        if s.get("stock_name"):
            nm.setdefault(sid, s["stock_name"])
    return sec, nm


def scan():
    prev, report_date = fd.prev_trading_date()
    rows, idx = fd.fetch_twse_t86(prev)
    if not rows:
        return {"report_date": report_date, "prev": prev, "error": "T86 無資料(休市?)"}
    sector_map, name_map = _info_maps()

    f_i = idx.get("外陸資買賣超股數(不含外資自營商)", 4)
    fs_i = idx.get("外資自營商買賣超股數", 7)
    t_i = idx.get("投信買賣超股數", 10)
    tot_i = idx.get("三大法人買賣超股數", 18)

    sectors = {}
    for row in rows:
        if len(row) <= tot_i:
            continue
        code = row[0].strip()
        sec = sector_map.get(code)
        if not sec or sec in fd.EXCLUDE_SECTORS:
            continue
        total = fd.parse_num(row[tot_i])
        foreign = fd.parse_num(row[f_i]) + fd.parse_num(row[fs_i])
        trust = fd.parse_num(row[t_i])
        s = sectors.setdefault(sec, {"net": 0, "trust": 0, "buy": 0, "sell": 0, "stocks": []})
        s["net"] += total
        s["trust"] += trust
        if total > 0:
            s["buy"] += 1
        elif total < 0:
            s["sell"] += 1
        s["stocks"].append({"code": code, "total": total, "trust": trust, "foreign": foreign})

    lots = lambda sh: round(sh / 1000)
    groups = []
    for sec, s in sectors.items():
        n = s["buy"] + s["sell"]
        if n < MIN_STOCKS:
            continue
        breadth = round(s["buy"] / n * 100) if n else 0
        formed = (s["net"] >= GROUP_MIN_LOTS * 1000 and breadth >= BROAD * 100 and s["buy"] >= MIN_STOCKS)
        leaders = sorted(s["stocks"], key=lambda x: x["total"], reverse=True)[:5]
        groups.append({
            "sector": sec, "net_lots": lots(s["net"]), "trust_lots": lots(s["trust"]),
            "buy_n": s["buy"], "sell_n": s["sell"], "breadth": breadth, "formed": formed,
            "leaders": [{"code": x["code"], "name": name_map.get(x["code"], ""),
                         "total_lots": lots(x["total"]), "trust_lots": lots(x["trust"])} for x in leaders],
        })
    groups.sort(key=lambda g: g["net_lots"], reverse=True)
    formed = [g for g in groups if g["formed"]]
    gate = "有明確族群成團" if formed else "族群不明顯,盤面難做→觀望"

    # 全市場法人買超王(給 Claude 重新分成股癌題材用的原料)
    all_stocks = [x for s in sectors.values() for x in s["stocks"]]
    top = sorted(all_stocks, key=lambda x: x["total"], reverse=True)[:40]
    top_stocks = [{"code": x["code"], "name": name_map.get(x["code"], ""),
                   "sector": sector_map.get(x["code"], ""), "total_lots": lots(x["total"]),
                   "trust_lots": lots(x["trust"]), "foreign_lots": lots(x["foreign"])} for x in top]
    for x in top_stocks[:PRICE_TOPN]:   # 只對前 N 名加價格確認(表態/創新高)
        try:
            x.update(_price_signal(x["code"], prev))
        except Exception:
            pass

    return {"report_date": report_date, "prev": prev, "gate": gate,
            "formed_count": len(formed), "groups": groups[:12], "top_stocks": top_stocks}


def main():
    out = scan()
    os.makedirs("data", exist_ok=True)
    with open("data/select_scan.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n===== 族群掃描 ({out.get('report_date')}，法人依 {out.get('prev')}) =====")
    if out.get("error"):
        print(out["error"]); return
    print(f"族群 gate：{out['gate']}（成團 {out['formed_count']} 個）\n")
    for g in out["groups"]:
        flag = "🔥成團" if g["formed"] else "  "
        print(f"{flag} {g['sector']}：淨買超 {g['net_lots']:+,}張 投信{g['trust_lots']:+,} "
              f"買{g['buy_n']}/賣{g['sell_n']}檔(廣度{g['breadth']}%)")
        led = "、".join(f"{x['code']}{x['name']}({x['total_lots']:+,})" for x in g["leaders"])
        print(f"      龍頭：{led}")
    print(f"\n----- 全市場法人買超王 + 表態確認(給 Claude 重新分股癌題材) -----")
    for x in out["top_stocks"][:PRICE_TOPN]:
        tag = ""
        if x.get("new52"): tag += " 🚀創52週新高"
        elif x.get("new20"): tag += " ⬆突破20日高"
        if x.get("near_high_pct") is not None: tag += f" 距高{x['near_high_pct']}%"
        if x.get("chg5") is not None: tag += f" 5日{x['chg5']:+}%"
        print(f"  {x['code']} {x['name']}　法人{x['total_lots']:+,}張(投信{x['trust_lots']:+,}/外資{x['foreign_lots']:+,})　[{x['sector']}]{tag}")


if __name__ == "__main__":
    main()
