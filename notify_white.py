#!/usr/bin/env python3
"""vwap.json 에서 'VWAP 아래에 있다가 막 뚫은' 종목 중 1분 거래대금이 늘고 있는 것만 ❕ 로 알려준다.
 - 5분봉·15분봉에서 지금 막 뚫음(k==0)
 - 1분 거래대금이 평소의 TVX_MIN 배 이상
 - 최근 1분 거래대금이 늘고 있음(첫 칸이 5칸 평균 이상)
 - 같은 코인·같은 봉은 COOLDOWN_H 시간 안에는 다시 알리지 않는다.
"""
import json, os
from datetime import datetime, timedelta, timezone
import requests

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "data", "vwap.json")
STATE = os.path.join(ROOT, "data", "notified_white.json")
BARS = ["5m", "15m"]
NAMES = {"5m": "5분", "15m": "15분"}
TVX_MIN = 2.0
COOLDOWN_H = 3
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


def rising(c):
    s = c.get("tv1s")
    if not s or len(s) < 5:
        return False
    return s[0] >= sum(s) / len(s)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or not os.path.exists(SRC):
        print("흰 느낌표 알림: 준비 안 됨")
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
    lines, new_keys = [], []

    for c in data.get("coins", []):
        sym = c["sym"]
        chg = c.get("chg", 0)
        x = c.get("tv1x")
        if x is None or x < TVX_MIN or not rising(c):
            continue
        for bar in BARS:
            r = (c.get("res") or {}).get(bar)
            if not r or r.get("k") != 0:
                continue
            key = f"w:{bar}:{sym}"
            if cooled(key):
                continue
            lines.append(f"{NAMES[bar]} · {sym} VWAP 아래에
