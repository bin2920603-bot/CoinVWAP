#!/usr/bin/env python3
"""vwap.json 에서 'VWAP 돌파'와 '전고점 돌파' 종목을 찾아 텔레그램으로 알려준다.
 - 같은 코인·같은 봉(또는 같은 전고점)은 6시간 안에는 다시 알리지 않는다.
 - notify_tf.py 가 끝난 뒤 이어서 실행된다.
"""
import json, os
from datetime import datetime, timedelta, timezone
import requests

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "data", "vwap.json")
STATE = os.path.join(ROOT, "data", "notified_vwap.json")
BARS = ["5m", "15m", "1H", "1D", "1W", "1M"]
NAMES = {"5m": "5분", "15m": "15분", "1H": "1시간", "1D": "일봉", "1W": "주봉", "1M": "월봉"}
LOOK = {"5m": 2, "15m": 1, "1H": 1, "1D": 1, "1W": 0, "1M": 0}
HIGHS = [("h120d", "일봉 120일 고점"), ("h20d", "일봉 20일 고점"), ("h3d", "1시간 3일 고점")]
JUST = 2.0
COOLDOWN_H = 6
MAX_LINES = 15
PAGE = "https://bin2920603-bot.github.io/CoinVWAP/"


def send(token, chat_id, text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
                          timeout=15)
        if r.status_code == 200:
            return True
        print(f"텔레그램 전송 실패: {r.status_code}")
    except Exception as e:
        print(f"텔레그램 전송 오류: {type(e).__name__}")
    return False


def prices():
    for host in ("https://www.okx.com", "https://aws.okx.com"):
        try:
            r = requests.get(host + "/api/v5/market/tickers", params={"instType": "SWAP"},
                             timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            j = r.json()
            if j.get("code") == "0":
                out = {}
                for t in j["data"]:
                    if t["instId"].endswith("-USDT-SWAP"):
                        try:
                            out[t["instId"].split("-")[0]] = float(t["last"])
                        except Exception:
                            pass
                return out
        except Exception:
            pass
    return {}


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or not os.path.exists(SRC):
        print("VWAP 알림: 준비 안 됨")
        return

    now = datetime.now(KST)
    state = {"sent": {}}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE, encoding="utf-8"))
        except Exception:
            state = {"sent": {}}
    sent = state.setdefault("sent", {})

    cut = now - timedelta(hours=24)
    for k in list(sent):
        try:
            if datetime.fromisoformat(sent[k]) < cut:
                del sent[k]
        except Exception:
            del sent[k]

    def cooled(key):
        try:
            return key in sent and now - datetime.fromisoformat(sent[key]) < timedelta(hours=COOLDOWN_H)
        except Exception:
            return False

    data = json.load(open(SRC, encoding="utf-8"))
    last = prices()
    vlines, hlines, new_keys = [], [], []

    for c in data.get("coins", []):
        sym = c["sym"]
        chg = c.get("chg", 0)
        tv = f" | 5분 거래대금 {c['tvx']}배" if c.get("tvx") is not None else ""

        for bar in BARS:
            r = (c.get("res") or {}).get(bar)
            if not r or r.get("k") is None or r["k"] > LOOK.get(bar, 0):
                continue
            key = f"v:{bar}:{sym}"
            if cooled(key):
                continue
            when = "지금" if r["k"] == 0 else f"{r['k']}봉전"
            vlines.append(f"{NAMES[bar]} · {sym} VWAP 돌파 ({when}) | 24시간 {chg:+.1f}%{tv}")
            new_keys.append(key)

        p = last.get(sym)
        if p:
            for k, label in HIGHS:
                h = c.get(k)
                if not h:
                    continue
                if h < p <= h * (1 + JUST / 100):
                    key = f"h:{k}:{sym}"
                    if not cooled(key):
                        hlines.append(f"{sym} {label} 돌파 | 고점 위 {(p / h - 1) * 100:+.1f}% | 24시간 {chg:+.1f}%{tv}")
                        new_keys.append(key)
                    break

    parts = []
    for title, lines in (("📈 VWAP 돌파 (새로 나온 것)", vlines), ("🚀 전고점 돌파 (새로 나온 것)", hlines)):
        if lines:
            more = f"\n(그 외 {len(lines) - MAX_LINES}개는 화면에서 확인)" if len(lines) > MAX_LINES else ""
            parts.append(title + "\n" + "\n".join(lines[:MAX_LINES]) + more)

    if parts:
        if send(token, chat_id, "\n\n".join(parts) + "\n\n" + PAGE):
            for k in new_keys:
                sent[k] = now.isoformat(timespec="seconds")
            print(f"VWAP 알림 {len(new_keys)}건 보냄")
        else:
            print("VWAP 알림 전송 실패 · 다음 번에 다시 시도")
    else:
        print("VWAP 알림: 새로 알릴 것 없음")

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
