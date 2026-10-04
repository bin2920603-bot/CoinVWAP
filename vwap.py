#!/usr/bin/env python3
"""OKX 선물 중 1분 거래대금이 막 늘어난 종목 30개를 골라 VWAP 돌파를 6가지 봉으로 검사해 data/vwap.json 에 저장한다.
추가: 전고점(1시간봉 3일 / 일봉 20일 / 일봉 120일) 가격, 최근 1분 거래대금 5개와 증가 배수도 함께 저장한다."""
import json, os, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import requests

HOSTS = ["https://www.okx.com", "https://aws.okx.com"]
KST = timezone(timedelta(hours=9))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vwap.json")
BARS = ["5m", "15m", "1H", "1D", "1W", "1M"]
TOP, PICK, N, MINB, LOOK = 150, 30, 100, 20, 2


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
    w = b[max(0, i - N + 1): i + 1]
    v = sum(x[3] for x in w)
    if v <= 0:
        return None
    return sum((x[0] + x[1] + x[2]) / 3 * x[3] for x in w) / v


def check(b):
    """b: 오래된 것 -> 최신. 각 칸은 (고가, 저가, 종가, 거래량)"""
    L = len(b) - 1
    if L < MINB + LOOK + 1:
        return None
    vn = vwap_at(b, L)
    if vn is None:
        return None
    if not b[L][2] > vn:
        return {"above": False, "k": None}
    for k in range(LOOK + 1):
        i = L - k
        vi, vp = vwap_at(b, i), vwap_at(b, i - 1)
        if vi is None or vp is None:
            break
        if b[i][2] > vi and b[i - 1][2] <= vp:
            return {"above": True, "k": k}
    return {"above": True, "k": None}


def tv_ratio(d):
    """1분봉 목록(최신이 맨 앞)에서 끝난 1분 거래대금 5개(최신부터)와,
    가장 최근 1분이 그 전 12분 평균의 몇 배인지."""
    done = [x for x in d if len(x) > 8 and x[8] == "1"]
    if len(done) >= 13:
        vals = [round(float(x[7])) for x in done[:5]]
        t1 = float(done[0][7])
        avg = sum(float(x[7]) for x in done[1:13]) / 12
        return vals, (round(t1 / avg, 2) if avg > 0 else None)
    return None, None


def extra(bar, d):
    """d: OKX 원본 봉 목록(최신이 맨 앞). 전고점 가격, 5분 거래대금을 계산한다."""
    try:
        if bar in ("1H", "1D"):
            highs = [float(x[2]) for x in reversed(d)]
            prior = highs[:-1]  # 지금 진행 중인 봉은 뺀다
            if bar == "1H":
                prior = prior[-72:]  # 3일
                if len(prior) >= 24:
                    return {"h3d": max(prior)}
            else:
                if len(prior) >= 10:
                    out = {"h20d": max(prior[-20:])}  # 20일
                    if len(prior) >= 30:
                        out["h120d"] = max(prior[-120:])  # 120일 (큰 고점)
                    return out
        elif bar == "5m":
            done = [x for x in d if len(x) > 8 and x[8] == "1"]  # 끝난 봉만
            if len(done) >= 13:
                t5 = float(done[0][7])    # 가장 최근에 끝난 5분
                t5p = float(done[1][7])   # 그 바로 전 5분
                avg = sum(float(x[7]) for x in done[1:13]) / 12
                return {"tv5": round(t5), "tv5p": round(t5p),
                        "tvx": round(t5 / avg, 2) if avg > 0 else None}
    except Exception:
        pass
    return {}


def rank(inst):
    """종목을 고르기 위해 1분 거래대금 5개와 증가 배수를 빠르게 본다."""
    d = get("/api/v5/market/candles", {"instId": inst, "bar": "1m", "limit": "20"})
    time.sleep(0.2)
    if not d:
        return inst, None, None
    vals, x1 = tv_ratio(d)
    return inst, vals, x1


def job(arg):
    inst, bar = arg
    d = get("/api/v5/market/candles", {"instId": inst, "bar": bar, "limit": "121" if bar == "1D" else "120"})
    time.sleep(0.2)
    if not d:
        return inst, bar, None, {}
    b = [(float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in reversed(d)]
    return inst, bar, check(b), extra(bar, d)


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
            coins.append({"sym": t["instId"].split("-")[0], "id": t["instId"],
                          "vol": vol, "chg": round((last / o - 1) * 100, 2), "res": {}})
    coins.sort(key=lambda c: -c["vol"])
    pool = coins[:TOP]
    with ThreadPoolExecutor(max_workers=4) as ex:
        ranks = {i: (v, x1) for i, v, x1 in ex.map(rank, [c["id"] for c in pool])}
    for c in pool:
        v, x1 = ranks.get(c["id"], (None, None))
        c["tv1s"], c["tv1x"] = v, x1
    pool.sort(key=lambda c: -(c.get("tv1x") or 0))
    coins = pool[:PICK]
    by = {c["id"]: c for c in coins}
    jobs = [(c["id"], bar) for c in coins for bar in BARS]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for inst, bar, res, more in ex.map(job, jobs):
            by[inst]["res"][bar] = res
            by[inst].update(more)
    for c in coins:
        del c["id"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated_at": datetime.now(KST).isoformat(timespec="seconds"), "coins": coins},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    hit = sum(1 for c in coins if any(r and r["k"] is not None for r in c["res"].values()))
    print(f"{len(coins)}종목 검사 · 돌파 {hit}종목")


if __name__ == "__main__":
    main()
