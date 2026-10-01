#!/usr/bin/env python3
"""OKX 선물을 여러 봉(15분·1시간·4시간·일봉)으로 보고 두 가지 타점 후보를 찾아 data/setup_tf.json 에 저장한다.
 1) breakout : 큰 양봉이 EMA7·20·50·200, VWAP100 선들을 막 뚫고 올라온 종목 (뚫은 직후)
 2) squeeze  : 선들이 좁게 모여 있고 가격이 그 근처에 붙어 있는, 아직 안 뚫은 종목 (뚫기 직전)
선이 모인 폭과 가격 거리는 '그 봉의 평소 캔들 크기의 몇 배'로 재서, 봉이 달라도 같은 기준을 쓴다.
"""
import json, os, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import requests

HOSTS = ["https://www.okx.com", "https://aws.okx.com"]
KST = timezone(timedelta(hours=9))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "setup_tf.json")

BARS = ["15m", "1H", "4H", "1D"]   # 볼 봉 (OKX 이름)
TOP = 300            # 거래대금 상위 몇 개 코인을 볼지
MIN_BARS = 60        # 봉이 이보다 적은 신규 상장은 제외
VWAP_N = 100         # VWAP을 계산할 최근 봉 수
RANGE_N = 50         # '평소 캔들 크기'를 잴 최근 봉 수

# --- 뚫은 직후 ---
BREAK_LOOK = 1       # 지금(0) 또는 한 봉 전(1) 뚫은 것까지 인정
BODY_MIN = 2.0       # 뚫은 양봉 몸통이 직전 20봉 평균 몸통의 몇 배 이상이어야 하는지
CROSS_MIN = 1        # 직전 봉까지 가격 위에 있던 선을 최소 몇 개 넘었는지

# --- 뚫기 직전 ---
SQ_SPREAD_X = 1.5    # EMA20·EMA50·VWAP100이 평소 캔들 크기의 이 배수 안에 모여 있어야 함
SQ_NEAR_X = 2.0      # 가격이 그 선들 평균에서 평소 캔들 크기의 이 배수 안에 있어야 함
SQ_CONTR = 1.2       # 최근 10봉 평균 변동폭이 직전 50봉 평균의 이 배수 이하
SQ_BODY_MAX = 2.0    # 지금 몸통이 평균의 이 배수 미만(아직 큰 양봉이 안 나옴)

# 주식·지수·금 등 코인이 아닌 종목: 빼지 않고 "stock" 표시만 달아서 화면에서 켜고 끌 수 있게 한다
STOCKS = {"SPY", "SPX", "QQQ", "XAU", "XCU", "AMZN", "GOOGL", "AVGO", "ADBE", "HPE", "TSLA",
          "ORCL", "NVDA", "AAPL", "MSFT", "META", "NFLX", "AMD"}


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


def ema_series(vals, n):
    k = 2 / (n + 1)
    out, e = [], vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def vwap_at(h, l, c, v, i, n=VWAP_N):
    s = max(0, i - n + 1)
    tv = sum(v[s:i + 1])
    if tv <= 0:
        return None
    return sum((h[j] + l[j] + c[j]) / 3 * v[j] for j in range(s, i + 1)) / tv


def analyze(o, h, l, c, v):
    """o,h,l,c,v: 오래된 것 -> 최신. {'breakout':..., 'squeeze':...} (없으면 키 없음)"""
    n = len(c)
    if n < MIN_BARS:
        return {}
    e7, e20, e50, e200 = (ema_series(c, p) for p in (7, 20, 50, 200))
    vw = [vwap_at(h, l, c, v, i) for i in range(n)]
    if vw[-1] is None:
        return {}

    def lines(i):
        return [e7[i], e20[i], e50[i], e200[i], vw[i]]

    def body(i):
        return abs(c[i] - o[i]) / o[i] if o[i] else 0

    def avg_body(i):
        s = max(0, i - 20)
        xs = [body(j) for j in range(s, i)]
        return sum(xs) / len(xs) if xs else 0

    L = n - 1
    rng = [(h[j] - l[j]) / c[j] for j in range(n)]
    rs = rng[max(0, n - RANGE_N):]
    avg_rng = sum(rs) / len(rs)            # 평소 캔들 크기(고저 폭 / 종가)
    res = {}

    # ---- 뚫은 직후 ----
    if c[L] > max(lines(L)):
        for k in range(BREAK_LOOK + 1):
            i = L - k
            if i < 1 or vw[i] is None or vw[i - 1] is None:
                break
            ln, lp = lines(i), lines(i - 1)
            if not (c[i] > max(ln) and c[i - 1] <= max(lp)):
                continue
            if c[i] <= o[i]:
                continue
            ab = avg_body(i)
            bx = body(i) / ab if ab > 0 else 0
            crossed = sum(1 for x in lp if c[i - 1] <= x and c[i] > x)
            if bx >= BODY_MIN and crossed >= CROSS_MIN:
                vs = v[max(0, i - 20):i]
                va = sum(vs) / max(1, len(vs))
                res["breakout"] = {
                    "k": k, "gain": round((c[i] / o[i] - 1) * 100, 1),
                    "body_x": round(bx, 1), "vol_x": round(v[i] / va, 1) if va > 0 else None,
                    "crossed": crossed,
                }
                break

    # ---- 뚫기 직전 ----
    if "breakout" not in res and avg_rng > 0:
        trio = [e20[L], e50[L], vw[L]]
        spread = (max(trio) - min(trio)) / c[L]
        mid = sum(trio) / 3
        near = abs(c[L] - mid) / mid
        r10 = rng[max(0, n - 10):]
        r50 = rng[max(0, n - 60):max(0, n - 10)]
        contr = (sum(r10) / len(r10)) / (sum(r50) / len(r50)) if r50 and sum(r50) > 0 else None
        ab = avg_body(L)
        bx_now = body(L) / ab if ab > 0 else 0
        sx, nx = spread / avg_rng, near / avg_rng
        if (sx <= SQ_SPREAD_X and nx <= SQ_NEAR_X and contr is not None
                and contr <= SQ_CONTR and bx_now < SQ_BODY_MAX):
            res["squeeze"] = {
                "spread_x": round(sx, 2), "near": round((c[L] / mid - 1) * 100, 2),
                "contr": round(contr, 2), "to200": round((e200[L] / c[L] - 1) * 100, 1),
            }
    return res


def job(arg):
    inst, bar = arg
    d = get("/api/v5/market/candles", {"instId": inst, "bar": bar, "limit": "300"})
    time.sleep(0.2)
    if not d:
        return inst, bar, None
    d = list(reversed(d))              # 오래된 것 -> 최신 (마지막 칸은 진행 중인 봉)
    try:
        o = [float(x[1]) for x in d]; h = [float(x[2]) for x in d]
        l = [float(x[3]) for x in d]; c = [float(x[4]) for x in d]
        v = [float(x[5]) for x in d]
    except Exception:
        return inst, bar, None
    return inst, bar, analyze(o, h, l, c, v)


def main():
    tk = get("/api/v5/market/tickers", {"instType": "SWAP"})
    if not tk:
        raise SystemExit("OKX 목록을 못 받음")
    coins = {}
    for t in tk:
        if not t["instId"].endswith("-USDT-SWAP"):
            continue
        last, o24 = float(t["last"] or 0), float(t["open24h"] or 0)
        vol = float(t["volCcy24h"] or 0) * last
        if vol > 0 and o24 > 0:
            coins[t["instId"]] = {"sym": t["instId"].split("-")[0], "vol": vol,
                                  "chg": round((last / o24 - 1) * 100, 1),
                                  "stock": t["instId"].split("-")[0] in STOCKS}
    top = sorted(coins, key=lambda k: -coins[k]["vol"])[:TOP]

    tfs = {b: {"breakout": [], "squeeze": []} for b in BARS}
    jobs = [(inst, b) for inst in top for b in BARS]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for inst, bar, r in ex.map(job, jobs):
            if not r:
                continue
            base = coins[inst]
            if "breakout" in r:
                tfs[bar]["breakout"].append({**base, **r["breakout"]})
            if "squeeze" in r:
                tfs[bar]["squeeze"].append({**base, **r["squeeze"]})
    for b in BARS:
        tfs[b]["breakout"].sort(key=lambda x: (x["k"], -x["crossed"], -x["body_x"]))
        tfs[b]["squeeze"].sort(key=lambda x: (x["spread_x"], abs(x["near"])))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated_at": datetime.now(KST).isoformat(timespec="seconds"),
               "checked": len(top), "bars": BARS, "tfs": tfs},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"{len(top)}종목 · " + " · ".join(
        f"{b}: 직전 {len(tfs[b]['squeeze'])}/직후 {len(tfs[b]['breakout'])}" for b in BARS))


if __name__ == "__main__":
    main()
