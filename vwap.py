#!/usr/bin/env python3
"""OKX 선물 상위 코인의 VWAP 돌파를 6가지 봉으로 검사해 data/vwap.json 에 저장한다."""
import json, os, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import requests

HOSTS = ["https://www.okx.com", "https://aws.okx.com"]
KST = timezone(timedelta(hours=9))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vwap.json")
BARS = ["5m", "15m", "1H", "1D", "1W", "1M"]
TOP, N, MINB, LOOK = 300, 100, 20, 2


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


def job(arg):
    inst, bar = arg
    d = get("/api/v5/market/candles", {"instId": inst, "bar": bar, "limit": "120"})
    time.sleep(0.2)
    if not d:
        return inst, bar, None
    b = [(float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in reversed(d)]
    return inst, bar, check(b)


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
    coins = coins[:TOP]
    by = {c["id"]: c for c in coins}
    jobs = [(c["id"], bar) for c in coins for bar in BARS]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for inst, bar, res in ex.map(job, jobs):
            by[inst]["res"][bar] = res
    for c in coins:
        del c["id"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated_at": datetime.now(KST).isoformat(timespec="seconds"), "coins": coins},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    hit = sum(1 for c in coins if any(r and r["k"] is not None for r in c["res"].values()))
    print(f"{len(coins)}종목 검사 · 돌파 {hit}종목")


if __name__ == "__main__":
    main()
