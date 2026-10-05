#!/usr/bin/env python3
"""OKX 선물 상위 100종목에서 1분봉 기준으로
'거가중(VWAP100) 아래에 있다가 막 위로 뚫고 올라온' 종목을 찾아 텔레그램으로 알린다.
1분 거래대금이 평소보다 늘 때만 알린다."""
import json, os, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import requests

HOSTS = ["https://www.okx.com", "https://aws.okx.com"]
KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "data", "notified_vwap1m.json")
TOP = 100         # 하루 거래대금 상위 몇 개를 볼지
N = 100           # 거가중 계산에 쓰는 봉 개수 (vwap.py와 같음)
LOOK = 2          # 뚫은 지 0~2봉 전까지만 잡는다
MAXGAP = 1.0      # 지금 종가가 거가중 위로 이만큼(%) 이내일 때만 잡는다
TVX_MIN = 2.0     # 방금 끝난 1분 거래대금이 직전 5개 평균의 몇 배 이상이어야 하는지
COOLDOWN_H = 1    # 같은 종목은 이 시간 안에 다시 알리지 않는다
MAX_LINES = 15
PAGE = "https://bin2920603-bot.github.io/CoinVWAP/"


def get(path, params=None):
    for i in range(4):
        for host in HOSTS:
            try:
                r = requests.get(host + path, params=params, timeout=10,
                                 headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 429:
                    time.sleep(1.5 * (i + 1))
                    continue
                j = r.json()
                if j.get("code") == "0":
                    return j["data"]
            except Exception:
                time.sleep(0.5)
    return None


def vwap_at(b, i):
    """b: 오래된 봉이 앞. i번째 봉까지 최근 N개 봉의 VWAP."""
    w = b[i - N + 1:i + 1]
    pv = sum(((float(x[2]) + float(x[3]) + float(x[4])) / 3) * float(x[5]) for x in w)
    v = sum(float(x[5]) for x in w)
    if v <= 0:
        return None
    return pv / v


def check(d):
    """d: OKX 1분봉 목록(최신이 맨 앞)."""
    if not d or len(d) < N + LOOK + 1:
        return None
    b = list(reversed(d))
    closes = [float(x[4]) for x in b]
    L = len(b) - 1
    vw = {}
    for i in range(L - LOOK - 1, L + 1):
        vw[i] = vwap_at(b, i)
        if vw[i] is None:
            return None
    if not closes[L] > vw[L]:
        return None
    gap = (closes[L] / vw[L] - 1) * 100
    if gap > MAXGAP:
        return None
    crossed = None
    for k in range(LOOK + 1):
        i = L - k
        if closes[i] > vw[i] and closes[i - 1] <= vw[i - 1]:
            crossed = k
            break
    if crossed is None:
        return None
    done = [x for x in d if len(x) > 8 and x[8] == "1"]
    if len(done) < 6:
        return None
    last = float(done[0][7])
    prev = [float(x[7]) for x in done[1:6]]
    avg = sum(prev) / len(prev)
    if avg <= 0:
        return None
    ratio = last / avg
    if ratio < TVX_MIN:
        return None
    return {"gap": round(gap, 2), "k": crossed, "tvx": round(ratio, 1)}


def job(inst):
    d = get("/api/v5/market/candles", {"instId": inst, "bar": "1m", "limit": "120"})
    time.sleep(0.2)
    return inst, check(d)


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


def main():
    tk = get("/api/v5/market/tickers", {"instType": "SWAP"})
    if not tk:
        raise SystemExit("OKX 목록을 못 받음")
    coins = []
    for t in tk:
        if not t["instId"].endswith("-USDT-SWAP"):
            continue
        last, o = float(t["last"] or 0), float(t["open24h"] or 0)
        vol = float(t["volCcy24h"] or 0) * last
        if vol > 0 and o > 0:
            coins.append({"sym": t["instId"].split("-")[0], "id": t["instId"], "vol": vol,
                          "chg": round((last / o - 1) * 100, 2)})
    coins.sort(key=lambda c: -c["vol"])
    coins = coins[:TOP]
    by = {c["id"]: c for c in coins}

    hits = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for inst, res in ex.map(job, [c["id"] for c in coins]):
            if res:
                c = by[inst]
                c["r"] = res
                hits.append(c)
    print(f"{len(coins)}종목 검사 · 1분 거가중 돌파 {len(hits)}종목")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("텔레그램 설정 없음")
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

    lines, new_keys = [], []
    for c in hits:
        key = f"w1:{c['sym']}"
        if key in sent:
            try:
                if now - datetime.fromisoformat(sent[key]) < timedelta(hours=COOLDOWN_H):
                    continue
            except Exception:
                pass
        r = c["r"]
        ago = "방금" if r["k"] == 0 else f"{r['k']}분 전"
        lines.append(f"{c['sym']} 거가중 위 {r['gap']:+.1f}% | {ago} 뚫음 | 1분 거래대금 {r['tvx']}배 | 24시간 {c['chg']:+.1f}%")
        new_keys.append(key)

    if not lines:
        print("1분 거가중 알림: 새로 알릴 것 없음")
    else:
        more = f"\n(그 외 {len(lines) - MAX_LINES}개는 생략)" if len(lines) > MAX_LINES else ""
        names = ", ".join(l.split(" ")[0] for l in lines[:MAX_LINES])
        text = "⚡ [거가중 1분] " + names + " 아래에서 뚫음\n" + "\n".join(lines[:MAX_LINES]) + more + "\n\n" + PAGE
        if send(token, chat_id, text):
            for k in new_keys:
                sent[k] = now.isoformat(timespec="seconds")
            print(f"1분 거가중 알림 {len(new_keys)}건 보냄")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
