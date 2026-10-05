#!/usr/bin/env python3
"""OKX 선물 상위 60종목에서 '200선 아래/근처에서 올라오면서 200선을 터치하고 올라가는 중 + 정배열이 만들어지는 중' 종목을
1분·5분·15분봉으로 찾고, 최근 1분 거래대금 5칸(늘고 있는지)도 같이 저장한다. 거래대금이 줄고 있으면 뺀다.
결과는 data/ema200.json 에 저장하고, 새로 나온 것은 텔레그램으로 알린다."""
import json, os, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import requests

HOSTS = ["https://www.okx.com", "https://aws.okx.com"]
KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "data", "ema200.json")
STATE = os.path.join(ROOT, "data", "notified_ema200.json")
BARS = ["1m", "5m", "15m"]
NAMES = {"1m": "1분", "5m": "5분", "15m": "15분"}
TOP = 60          # 하루 거래대금 상위 몇 개를 볼지
MAXGAP = 1.0      # 지금 종가가 200선 위로 이만큼(%) 이내일 때만 잡는다
TOUCH = 0.05      # 저가가 200선 위 이만큼(%) 이내이거나 200선 아래면 '닿았다'
BACK = 6          # 터치가 이 봉 수 이내(0~5봉 전)일 때만 잡는다
PRE = 5           # 터치 직전 이 봉 수 동안 종가가 200선 아래/근처였어야 한다(떨어지면서 닿은 것 제외)
NEAR = 0.15       # '근처'로 보는 범위(%): 200선 위 이만큼까지는 아래/근처로 본다
ALLOW = ("7>20", "7>20>50")   # 정배열이 완성되기 전(노랑·주황) 단계만 잡는다
DROP_FALLING = True           # 최근 1분 거래대금이 줄고 있으면 뺀다
COOLDOWN_H = 3    # 같은 종목·같은 봉은 이 시간 안에 다시 알리지 않는다
MAX_LINES = 15
PAGE = "https://bin2920603-bot.github.io/CoinVWAP/ema200.html"


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


def ema(vals, n):
    k = 2.0 / (n + 1)
    out, e = [], vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def check(d):
    """d: OKX 원본 봉 목록(최신이 맨 앞)."""
    if not d or len(d) < 210:
        return None
    b = list(reversed(d))
    lows = [float(x[3]) for x in b]
    closes = [float(x[4]) for x in b]
    e7, e20, e50, e200 = (ema(closes, n) for n in (7, 20, 50, 200))
    L = len(closes) - 1
    if not closes[L] > e200[L]:
        return None
    gap = (closes[L] / e200[L] - 1) * 100
    if gap > MAXGAP:
        return None
    if not (e7[L] > e20[L] and e7[L] > e7[L - 3]):
        return None

    touched = None
    for k in range(BACK):
        i = L - k
        if lows[i] <= e200[i] * (1 + TOUCH / 100):
            touched = k
            break
    if touched is None:
        return None
    t = L - touched

    pre = range(max(0, t - PRE), t)
    if len(pre) < 2:
        return None
    for i in pre:
        if closes[i] > e200[i] * (1 + NEAR / 100):
            return None

    if touched > 0 and not closes[L] > closes[t]:
        return None
    if touched > 0 and not any(closes[i] > e200[i] for i in range(t + 1, L + 1)):
        return None

    stage = "7>20"
    if e20[L] > e50[L]:
        stage = "7>20>50"
        if e50[L] > e200[L]:
            stage = "7>20>50>200"
    if stage not in ALLOW:
        return None
    return {"gap": round(gap, 2), "touch": touched, "stage": stage}


def tv_info(d):
    """1분봉 목록(최신이 맨 앞)에서 끝난 1분 거래대금 5개(최신부터)와 증가/감소."""
    done = [x for x in d if len(x) > 8 and x[8] == "1"]
    if len(done) < 6:
        return None
    vals = [round(float(x[7])) for x in done[:5]]
    avg = sum(vals) / len(vals)
    trend = "up" if vals[0] >= avg else "down"
    return {"tv1s": vals, "trend": trend}


def job(arg):
    inst, bar = arg
    d = get("/api/v5/market/candles", {"instId": inst, "bar": bar, "limit": "300"})
    time.sleep(0.2)
    return inst, bar, check(d)


def tv_job(inst):
    d = get("/api/v5/market/candles", {"instId": inst, "bar": "1m", "limit": "20"})
    time.sleep(0.2)
    return inst, (tv_info(d) if d else None)


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
                          "chg": round((last / o - 1) * 100, 2), "last": last, "res": {}})
    coins.sort(key=lambda c: -c["vol"])
    coins = coins[:TOP]
    by = {c["id"]: c for c in coins}
    jobs = [(c["id"], bar) for c in coins for bar in BARS]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for inst, bar, res in ex.map(job, jobs):
            if res:
                by[inst]["res"][bar] = res

    # 패턴이 잡힌 종목만 1분 거래대금 5칸을 추가로 받아 늘고 있는지 본다
    cand = [c for c in coins if c["res"]]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for inst, info in ex.map(tv_job, [c["id"] for c in cand]):
            if info:
                by[inst].update(info)
    hits = []
    for c in cand:
        if DROP_FALLING and c.get("trend") == "down":
            continue
        hits.append(c)
    for c in hits:
        del c["id"]

    now = datetime.now(KST)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated_at": now.isoformat(timespec="seconds"), "scanned": len(coins), "coins": hits},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"{len(coins)}종목 검사 · 200선 패턴 {len(cand)}종목 · 거래대금 감소 제외 후 {len(hits)}종목")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return

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
        for bar in BARS:
            r = c["res"].get(bar)
            if not r:
                continue
            key = f"e:{bar}:{c['sym']}"
            if key in sent:
                try:
                    if now - datetime.fromisoformat(sent[key]) < timedelta(hours=COOLDOWN_H):
                        continue
                except Exception:
                    pass
            lines.append(f"{NAMES[bar]} · {c['sym']} 200선 위 {r['gap']:+.1f}% | 정배열 {r['stage']} | 24시간 {c['chg']:+.1f}%")
            new_keys.append(key)

    if lines:
        more = f"\n(그 외 {len(lines) - MAX_LINES}개는 화면에서 확인)" if len(lines) > MAX_LINES else ""
        text = "❗ [200선] " + ", ".join(sorted({l.split(" · ")[1].split(" ")[0] for l in lines[:MAX_LINES]})) + " 터치 후 상승\n" + "\n".join(lines[:MAX_LINES]) + more + "\n\n" + PAGE
        if send(token, chat_id, text):
            for k in new_keys:
                sent[k] = now.isoformat(timespec="seconds")
            print(f"200선 알림 {len(new_keys)}건 보냄")
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
