#!/usr/bin/env python3
"""Daily Taiwan stock report: FinMind data + Claude Code CLI web_search + Slack."""
import os
import re
import subprocess
import requests
from datetime import datetime, timedelta

HOLDINGS = [
    {"code": "6274", "name": "台燿科技", "sector": "CCL"},
    {"code": "2327", "name": "國巨", "sector": "被動元件"},
]
SLACK_CHANNEL = "C0AU8BK7GQZ"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def prev_trading_date():
    now_tw = datetime.utcnow() + timedelta(hours=8)
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


def build_context(prev_date):
    lines = ["【FinMind 已抓取資料（直接使用，不需再查詢收盤價與法人）】\n"]
    for stock in HOLDINGS:
        p = finmind_price(stock["code"], prev_date)
        inst = finmind_inst(stock["code"], prev_date)
        if not p:
            lines.append(f"{stock['name']}（{stock['code']}）：無資料（可能休市）\n")
            continue
        spread = p["spread"]
        close = p["close"]
        pct = round(spread / (close - spread) * 100, 2) if (close - spread) else 0
        def s(v): return f"+{v}" if v >= 0 else str(v)
        lines += [
            f"{stock['name']}（{stock['code']}）{stock['sector']}",
            f"  收盤價（{prev_date}）：{close}　漲跌：{s(spread)}（{s(pct)}%）",
            f"  三大法人：{inst['text']}",
            f"  投信單日：{s(inst['trust'])}張\n",
        ]
    return "\n".join(lines)


def run_analysis(prompt):
    proc = subprocess.run(
        ["claude", "--print", "--dangerously-skip-permissions"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=1500,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Claude CLI error:\n{proc.stderr[:2000]}")
    return proc.stdout


def slack_send(token, channel, text, thread_ts=None):
    data = {"channel": channel, "text": text}
    if thread_ts:
        data["thread_ts"] = thread_ts
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json=data, timeout=15,
    )
    result = r.json()
    if not result.get("ok"):
        raise RuntimeError(f"Slack error: {result.get('error')}")
    return result["ts"]


def parse_sections(text):
    parts = re.split(r"#{3}\s*MSG\s*(\d+)\s*#{3}", text)
    sections = {}
    for i in range(1, len(parts), 2):
        num = int(parts[i])
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if content:
            sections[num] = content
    return sections


def main():
    prev_date, report_date = prev_trading_date()
    data_ctx = build_context(prev_date)

    prompt = f"""你是一位專業的股票研究分析師，今天請完成兩個分析任務，全程用繁體中文。

{data_ctx}

報告日期：{report_date}（台灣時區）
資料日期（前一交易日）：{prev_date}

⚠️ 規則：
- 收盤價、三大法人已在上方提供，直接使用，不需再搜尋
- RSI/MACD/KDJ、EPS、月營收、市場消息、動能選股 → 用 web_search 搜尋
- 技術指標搜尋格式：「[代號] [名稱] RSI MACD KDJ {prev_date}」（帶日期防抓舊資料）

請用以下格式輸出，每個區塊以 ### MSG n ### 起頭：

### MSG 1 ###
📈 *每日股票報告 {report_date}*
📌 今日重點：（3 句話摘要最值得注意的訊號）
🚀 動能推薦：（第一名股票名稱＋一句理由）
⭐ 投信買超王：（投信買超量最大個股名稱）
👇 詳細分析請點 thread

### MSG 2 ###
（台燿科技 6274 完整分析）

### MSG 3 ###
（國巨 2327 完整分析）

### MSG 4 ###
🚀 動能選股推薦 — 類群輪動 × 基本面驗證
━━━━━━━━━━━━━━━━━━━━
📍 今日強勢類群：[名稱]（淨買超金額若有請列）
選股邏輯：一句話
⭐ 投信買超王：[名稱＋買超張數]
🥇 第一名：[名稱（代號）]　🥈 第二名：[名稱（代號）]　🥉 第三名：[名稱（代號）]
⚠️ 風險提示：動能策略在市場急跌或類群反轉時失效快，請嚴守停損。

### MSG 5 ###
（動能第一名 完整分析）

### MSG 6 ###
（動能第二名 完整分析）

### MSG 7 ###
（動能第三名 完整分析）

━━━━━━━━━━━━━━━━━━━━
【標準分析格式】每支股票均使用：
━━━━━━━━━━━━━━━━━━━━
【股票名稱（代號）】收盤價（YYYY-MM-DD） 漲跌%
📊 技術指標：RSI、MACD多空、KDJ（標注資料日）
📋 基本面：月營收/EPS 一句話
🏦 籌碼面：外資投信自營買賣超（張數）
🏛️ 法人動向：前三大法人持倉變化
📈 投信動向：單日買賣超（張數）＋近期加碼/減碼趨勢
🌍 外資動向：外資持股比例變化
🔍 市場消息：漲價通知、供貨吃緊、急單、小道消息
💬 市場情緒：一句話
⚔️ 競爭對手：一句話
💰 報價動態：一句話
📦 新客戶/大單：一句話
🔗 供應鏈：一句話
🌐 產業趨勢：含美國同類股/ETF動向
🇺🇸 美國市場動向：一句話
✅ 結論：偏多/偏空/觀望 + 一句理由
━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━
【任務一】兩支持股分析
━━━━━━━━━━━━━━━━━━━━
持股：6274 台燿科技（CCL）、2327 國巨（被動元件）

搜尋策略（每支執行兩層）：
第一層：搜尋「[股票名稱] 最新消息」
第二層：
- 台燿 → Rogers Corp、Isola、AI PCB 需求
- 國巨 → TDK、Murata、太陽誘電、被動元件市場

━━━━━━━━━━━━━━━━━━━━
【任務二】動能選股推薦
━━━━━━━━━━━━━━━━━━━━
步驟一：搜尋「台股 類股 資金流向 今日」或「台股族群輪動 今日買超」
→ 找外資+投信淨買超最多的前 1-2 個產業別
→ 若結果太舊，改搜「台股強勢族群 近三日」

步驟二：搜尋「[類群名稱] 台股 個股 月營收 EPS 法人買超」
→ 篩選：動能面（近一月漲幅前段、量能放大、RSI<75）+ 基本面 + 籌碼面
→ ⭐ 特別留意投信近五日買超最大個股（標注「投信重點買超」）

⚠️ 驗證（必做）：對每支候選股票搜尋「[公司名稱] 股票代號」確認吻合，代號有誤直接修正
"""

    print(f"Report date: {report_date}, Data date: {prev_date}")
    print("Running Claude analysis with web_search...")
    result = run_analysis(prompt)

    print("Parsing sections...")
    sections = parse_sections(result)
    print(f"Found {len(sections)} sections")

    if not sections:
        print("ERROR: No sections found. Raw response (first 500 chars):")
        print(result[:500])
        raise SystemExit(1)

    print("Sending to Slack...")
    token = os.environ["SLACK_BOT_TOKEN"]
    ts = slack_send(token, SLACK_CHANNEL, sections.get(1, "⚠️ 報告生成失敗"))
    for i in range(2, 8):
        if i in sections:
            slack_send(token, SLACK_CHANNEL, sections[i], thread_ts=ts)
            print(f"  Sent MSG {i}")

    print("Done!")


if __name__ == "__main__":
    main()
