import os
import json
import requests
import yfinance as yf
import pandas as pd

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {
            "commodity_regime": "UNKNOWN",
            "risk_score": 0
        }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

SYMBOLS = {
    "QQQ": "QQQ",
    "NVDA": "NVDA",
    "VIX": "^VIX",
    "10Y": "^TNX",
    "DXY": "DX-Y.NYB",
    "GLD": "GLD",
    "SLV": "SLV",
}

def get_data(symbol, period="3mo"):
    df = yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"No data for {symbol}")
    return df

def get_close_series(df):
    close = df["Close"]

    # yfinance sometimes returns a DataFrame instead of Series
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    return close.dropna()

def latest_close(df):
    close = get_close_series(df)
    return float(close.iloc[-1])

def drawdown_from_20d_high(df):
    close = get_close_series(df)
    high_20 = close.tail(20).max()
    latest = close.iloc[-1]
    return float((latest / high_20 - 1) * 100)

def five_day_change(df):
    close = get_close_series(df)
    if len(close) < 6:
        return 0.0
    return float((close.iloc[-1] / close.iloc[-6] - 1) * 100)

def score_signals(data):
    score = 0
    notes = []

    ten_y = data["10Y"]["price"]  # ^TNX quotes 10x yield
    dxy = data["DXY"]["price"]
    vix = data["VIX"]["price"]
    qqq_dd = data["QQQ"]["dd20"]
    nvda_dd = data["NVDA"]["dd20"]
    ten_y_5d = data["10Y"]["chg5"]
    qqq_5d = data["QQQ"]["chg5"]

    if ten_y > 5.0:
        score += 3
        notes.append(f"10Y > 5.0% danger zone ({ten_y:.2f}%)")
    elif ten_y > 4.7:
        score += 2
        notes.append(f"10Y > 4.7% valuation pressure ({ten_y:.2f}%)")
    elif ten_y > 4.3:
        score += 2
        notes.append(f"10Y > 4.3% caution zone ({ten_y:.2f}%)")

    if dxy > 106:
        score += 2
        notes.append(f"DXY > 106 ({dxy:.2f})")

    if vix > 25:
        score += 2
        notes.append(f"VIX > 25 risk-off ({vix:.2f})")
    elif vix > 20:
        score += 1
        notes.append(f"VIX > 20 stress rising ({vix:.2f})")

    if qqq_dd < -8:
        score += 2
        notes.append(f"QQQ drawdown > 8% ({qqq_dd:.1f}%)")
    elif qqq_dd < -5:
        score += 1
        notes.append(f"QQQ drawdown > 5% ({qqq_dd:.1f}%)")

    if nvda_dd < -12:
        score += 2
        notes.append(f"NVDA drawdown > 12% ({nvda_dd:.1f}%)")
    elif nvda_dd < -8:
        score += 1
        notes.append(f"NVDA drawdown > 8% ({nvda_dd:.1f}%)")

    slv_dd = data["SLV"]["dd20"]

    if slv_dd < -12 and ten_y < 4.7 and dxy < 105:
        notes.append(f"SLV deep tactical zone forming: 20D drawdown {slv_dd:.1f}%")
    elif slv_dd < -8 and ten_y < 4.7 and dxy < 105:
        notes.append(f"SLV watch zone only: 20D drawdown {slv_dd:.1f}%")
    
    if ten_y_5d > 3 and qqq_5d > 0:
        score += 2
        notes.append("Danger divergence: 10Y rising while QQQ rising")

    gld_5d = data["GLD"]["chg5"]
    slv_5d = data["SLV"]["chg5"]
    
    # Commodity macro pressure
    if ten_y > 4.7 and dxy > 105:
        score += 2
        notes.append("Macro pressure against metals: high yields + strong dollar")
    
    # Silver weak while dollar rising
    if slv_5d < -3 and dxy > 105:
        score += 1
        notes.append("Silver weakening under dollar pressure")
    
    # Gold defensive bid
    if gld_5d > 2 and vix > 20:
        notes.append("Gold acting as defensive hedge")
        
    return min(score, 10), notes
    
def commodity_regime(data):
    ten_y = data["10Y"]["price"]
    dxy = data["DXY"]["price"]
    vix = data["VIX"]["price"]

    if ten_y > 4.7 and dxy > 105:
        return "METAL HEADWIND"

    if ten_y < 4.4 and dxy < 103:
        return "METAL SUPPORTIVE"

    if vix > 25:
        return "VOLATILE / DEFENSIVE"

    return "NEUTRAL"
    
def action_from_score(score):
    if score <= 2:
        return "NORMAL: stay invested, no chase."
    if score <= 4:
        return "CAUTION: avoid chasing AI; keep cash."
    if score <= 6:
        return "DEFENSIVE: add only by preset pullback levels."
    if score <= 8:
        return "RISK-OFF: pause new buys; review exposure."
    return "PANIC: act only by rule; prepare staged buys after stabilization."

def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }
    r = requests.post(url, json=payload, timeout=20)

    if not r.ok:
        print("Telegram error:", r.status_code, r.text)

    r.raise_for_status()

def main():
    data = {}

    previous_state = load_state()
    
    for name, symbol in SYMBOLS.items():
        df = get_data(symbol)
        data[name] = {
            "price": latest_close(df),
            "dd20": drawdown_from_20d_high(df),
            "chg5": five_day_change(df),
        }

    score, notes = score_signals(data)
    if regime_changed:
        notes.append(
            f"Commodity regime changed: {previous_regime} → {commodity_state}"
        )
    action = action_from_score(score)
    commodity_state = commodity_regime(data)

    previous_regime = previous_state.get("commodity_regime", "UNKNOWN")

    regime_changed = previous_regime != commodity_state
    
    ten_y = data["10Y"]["price"]

    message = f"""
*Macro Gravity Daily Alert*

*Risk Score:* {score}/10
*Action:* {action}
*Commodity Regime:* {commodity_state}

*Key Levels*
- 10Y: {ten_y:.2f}%
- DXY: {data["DXY"]["price"]:.2f}
- VIX: {data["VIX"]["price"]:.2f}
- QQQ 20D drawdown: {data["QQQ"]["dd20"]:.1f}%
- NVDA 20D drawdown: {data["NVDA"]["dd20"]:.1f}%
- SLV 20D drawdown: {data["SLV"]["dd20"]:.1f}%

*Commodity Regime*
- GLD 5D: {data["GLD"]["chg5"]:.1f}%
- SLV 5D: {data["SLV"]["chg5"]:.1f}%

*5D Moves*
- 10Y: {data["10Y"]["chg5"]:.1f}%
- QQQ: {data["QQQ"]["chg5"]:.1f}%
- NVDA: {data["NVDA"]["chg5"]:.1f}%




*Triggered Signals*
{chr(10).join(["- " + n for n in notes]) if notes else "- None"}

*Discipline*
Do not chase. Deploy only on preset pullback levels.
"""
    save_state({
    "commodity_regime": commodity_state,
    "risk_score": score
    })
    send_telegram(message.strip())

if __name__ == "__main__":
    main()
