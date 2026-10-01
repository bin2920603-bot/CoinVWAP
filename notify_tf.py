#!/usr/bin/env python3
"""setup_tf.json 에서 '뚫은 직후' 후보를 찾아 텔레그램으로 알려준다.
 - 같은 코인·같은 봉은 6시간 안에는 다시 알리지 않는다.
 - 주식·지수·금 종목은 한국시간 밤 10시~새벽 6시에만 알린다.
 - 처음 한 번은 "알림 연결됐어요" 인사 메시지를 보낸다.
토큰과 번호는 GitHub secret(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)에서 읽는다. 화면이나 파일에 찍지 않는다.
"""
import json, os
from datetime import datetime, timedelta, timezone
import requests

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "data", "setup_tf.json")
STATE = os.path.join(ROOT, "data", "notified.json")
NAMES = {"15m": "15분", "1H": "1시간", "4H": "4시간", "1D": "일봉"}
MAX_LOOK = 1         # 지금(0)이나 한 봉 전(1)에 뚫은 것까지 알린다
COOLDOWN_H = 6       # 같은 코인·봉은 이 시간 안에는 다시 알리지 않는다
MAX_LINES = 15       # 한 번에 보낼 최대 줄 수
PAGE = "https://bin2920603-bot.github.io/CoinVWAP/setup_tf.html"


def send(token, chat_id, text):
    """성공하면 True. 오류 내용에 토큰이 섞이지 않게 종류만 출력한다."""
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
                          timeout=15)
        if r.status_code == 200:
            return True
        try:
            desc = r.json().get("description", "")
        except Exception:
            desc = ""
        print(f"텔레그램 전송 실패: {r.status_code} {desc}")
    except Exception as e:
        print(f"텔레그램 전송 오류: {type(e).__name__}")
    return False


def stock_hours(now):
    return now.hour >= 22 or now.hour < 6


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 가 비어 있어요")

    now = datetime.now(KST)
    first = not os.path.exists(STATE)
    state = {"sent": {}}
    if not first:
        try:
            state = json.load(open(STATE, encoding="utf-8"))
        except Exception:
            state = {"sent": {}}
    sent = state.setdefault("sent", {})

    if first:
        ok = send(token, chat_id,
                  "✅ 타점 알림이 연결됐어요.\n앞으로 '뚫은 직후' 코인이 새로 나오면 여기로 알려드려요.")
        if not ok:
            raise SystemExit("인사 메시지를 못 보냈어요. 토큰과 번호를 확인해 주세요")

    # 오래된 기록 정리
    cut = now - timedelta(hours=24)
    for k in list(sent):
        try:
            if datetime.fromisoformat(sent[k]) < cut:
                del sent[k]
        except Exception:
            del sent[k]

    lines, new_keys = [], []
    if os.path.exists(SRC):
        data = json.load(open(SRC, encoding="utf-8"))
        for bar in data.get("bars", []):
            for c in data["tfs"].get(bar, {}).get("breakout", []):
                if c["k"] > MAX_LOOK:
                    continue
                if c.get("stock") and not stock_hours(now):
                    continue
                key = f"{bar}:{c['sym']}"
                if key in sent:
                    try:
                        if now - datetime.fromisoformat(sent[key]) < timedelta(hours=COOLDOWN_H):
                            continue
                    except Exception:
                        pass
                when = "지금" if c["k"] == 0 else "1봉전"
                vol = f" | 거래량 {c['vol_x']}배" if c.get("vol_x") is not None else ""
                tag = " [주식]" if c.get("stock") else ""
                lines.append(f"{NAMES.get(bar, bar)} · {c['sym']}{tag} {when} 양봉 {c['gain']:+.1f}% | "
                             f"몸통 {c['body_x']}배{vol} | 선 {c['crossed']}개 넘음")
                new_keys.append(key)

    if lines:
        shown = lines[:MAX_LINES]
        more = f"\n(그 외 {len(lines) - MAX_LINES}개는 화면에서 확인)" if len(lines) > MAX_LINES else ""
        text = "🔔 뚫은 직후 (새로 나온 것)\n" + "\n".join(shown) + more + "\n\n" + PAGE
        if send(token, chat_id, text):
            for k in new_keys:
                sent[k] = now.isoformat(timespec="seconds")
            print(f"알림 {len(new_keys)}건 보냄")
        else:
            print("알림 전송 실패 · 다음 번에 다시 시도")
    else:
        print("새로 알릴 것 없음")

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
