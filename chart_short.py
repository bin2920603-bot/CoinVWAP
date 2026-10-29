import os, json, time
import requests
from chartpush import (candles_cached, make_chart, ema_full, rsi6, BAR_MS,
                       vol_trend, TREND_TXT, verdict, big_picture,
                       counter_of, counter_line)

HOSTS = ["https://www.okx.com", "https://aws.okx.com"]
NL = chr(10)
TOP_N = 60
BARS = [("15m", "15분"), ("1H", "1시간"), ("4H", "4시간")]
MAX_ALERTS = 10
COOL_BARS = 3
RSI_HI = 70
RSI_MIN = 60
LIVE_MIN_CHG = -0.5
VWAP_N = 100
MAXGAP = 1.0
LOOK = 1
TOUCH = 0.05
BACK = 6
PRE = 5
NEAR = 0.15
VWAP_VOL_MIN = 1.5
VOL_AVG = 20
ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "data", "short.json")


def get(path, params=None):
    for host in HOSTS:
        try:
            r = requests.get(host + path, params=params, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
            j = r.json()
            if j.get("code") == "0":
                return j["data"]
        except Exception:
            pass
    return None


def vwap_at(cs, i):
    w = cs[i - VWAP_N + 1:i + 1]
    v = sum(float(x[5]) for x in w)
    if v <= 0:
        return None
    pv = sum(((float(x[2]) + float(x[3]) + float(x[4])) / 3) * float(x[5]) for x in w)
    return pv / v


def vol_ratio(cs, bar):
    n = len(cs)
    dur = BAR_MS.get(bar, 3600000)
    elapsed = int(time.time() * 1000) - int(cs[-1][0])
    live = cs[-1][8] == "0"
    if live and elapsed < dur * 0.1:
        ki, scale = n - 2, 1.0
    elif live:
        ki, scale = n - 1, dur / min(elapsed, dur)
    else:
        ki, scale = n - 1, 1.0
    cur_vol = float(cs[ki][5]) * scale
    past = [float(x[5]) for x in cs[max(0, ki - VOL_AVG):ki]]
    avg = sum(past) / len(past) if past else 0
    return cur_vol / avg if avg > 0 else 0


def detect(cs, bar):
    """숏 신호 목록 [(종류, 글)]. 우선순위 순서."""
    hits = []
    n = len(cs)
    L = n - 1
    closes = [float(x[4]) for x in cs]
    highs = [float(x[2]) for x in cs]
    vx = vol_ratio(cs, bar)

    now_r = rsi6(closes)
    prev_r = rsi6(closes[:-1])
    o = float(cs[-1][1])
    chg = (closes[-1] / o - 1) * 100 if o else 0
    if now_r is not None and prev_r is not None:
        if prev_r >= RSI_HI > now_r >= RSI_MIN and chg <= LIVE_MIN_CHG:
            hits.append(("rsi", "RSI70 하향 이탈 (RSI6 " + format(prev_r, ".1f")
                         + "→" + format(now_r, ".1f") + ")"))

    e200 = ema_full(closes, 200)
    e20 = ema_full(closes, 20)
    e7 = ema_full(closes, 7)
    if e200[L] is not None and closes[L] < e200[L]:
        gap = (closes[L] / e200[L] - 1) * 100
        ok = gap >= -MAXGAP and e7[L] < e20[L] and e7[L] < e7[L - 3]
        touched = None
        if ok:
            for k in range(BACK):
                i = L - k
                if e200[i] is not None and highs[i] >= e200[i] * (1 - TOUCH / 100):
                    touched = k
                    break
        if ok and touched is not None:
            t = L - touched
            pre = range(max(0, t - PRE), t)
            if len(pre) >= 2 and all(e200[i] is not None for i in pre):
                for i in pre:
                    if closes[i] < e200[i] * (1 - NEAR / 100):
                        ok = False
                if ok and touched > 0 and not closes[L] < closes[t]:
                    ok = False
                if ok and touched > 0 and not any(
                        closes[i] < e200[i] for i in range(t + 1, L + 1)):
                    ok = False
                if ok:
                    hits.append(("e200", "200선 저항 후 하락 (200선 대비 "
                                 + format(gap, "+.1f") + "%)"))

    if n >= VWAP_N + LOOK + 2 and vx >= VWAP_VOL_MIN:
        vw = {}
        for i in range(L - LOOK - 1, L + 1):
            vw[i] = vwap_at(cs, i)
        if all(v is not None for v in vw.values()) and closes[L] < vw[L]:
            gap = (closes[L] / vw[L] - 1) * 100
            if gap >= -MAXGAP:
                for k in range(LOOK + 1):
                    i = L - k
                    if closes[i] < vw[i] and closes[i - 1] >= vw[i - 1]:
                        when = "지금" if k == 0 else str(k) + "봉전"
                        hits.append(("vwap", "VWAP 아래로 뚫음 (" + when + ")"))
                        break
    return hits


def classify_short(t):
    d, h4, h1 = t["1D"], t["4H"], t["1H"]
    if d is None:
        return "⚪ 판단 보류 (일봉 자료가 부족해요)"
    if d == "down" and h4 in ("down", "mix"):
        return "🔻 되돌림 숏 (일봉 하락 추세 속 반등 뒤 재하락)"
    if d == "down":
        return "🟡 반등 중 하락 (일봉은 하락, 4시간이 아직 위)"
    if h4 == "down" and h1 == "down":
        return "🟠 추세 전환 초기 (찐 하락 가능성, 일봉 확인 필요)"
    return "🟡 기술적 하락 (큰 틀은 위, 짧게 꺾이는 중)"


def send_photo(token, chat, path, caption):
    url = "https://api.telegram.org/bot" + token + "/sendPhoto"
    try:
        with open(path, "rb") as f:
            r = requests.post(url, data={"chat_id": chat, "caption": caption[:1000]},
                              files={"photo": f}, timeout=30)
        return r.status_code == 200
    except Exception:
        return False


def send_text(token, chat, text):
    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat, "text": text}, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("텔레그램 설정 없음")
        return
    sent = {}
    if os.path.exists(STATE):
        try:
            sent = json.load(open(STATE, encoding="utf-8"))
        except Exception:
            sent = {}
    now = int(time.time() * 1000)

    tk = get("/api/v5/market/tickers", {"instType": "SWAP"})
    if not tk:
        print("OKX 목록을 못 받음")
        return
    rows = []
    for t in tk:
        if not t["instId"].endswith("-USDT-SWAP"):
            continue
        try:
            rows.append((float(t["volCcy24h"]) * float(t["last"]), t["instId"]))
        except ValueError:
            pass
    rows.sort(reverse=True)

    count = 0
    for vol, inst in rows[:TOP_N]:
        if count >= MAX_ALERTS:
            break
        sym = inst.replace("-USDT-SWAP", "")
        for bar, label in BARS:
            if count >= MAX_ALERTS:
                break
            raw = candles_cached(inst, bar)
            if not raw:
                continue
            cs = list(reversed(raw))
            if len(cs) < 210:
                continue
            pick = None
            for kind, text in detect(cs, bar):
                key = inst + "|" + bar + "|" + kind
                last_ts = sent.get(key)
                if last_ts and now - int(last_ts) < COOL_BARS * BAR_MS[bar]:
                    continue
                pick = (key, text)
                break
            if pick is None:
                continue
            key, text = pick
            tr = big_picture(inst)
            ok, vline, tline = verdict(raw, bar, "short")
            bad = counter_of(tr, "short")
            is_tp = ok and not bad
            lines = ["🔻 숏 타점" if is_tp else "🟡 숏 신호 (관망)",
                     label + " · " + sym + " " + text]
            if vline:
                lines.append(vline)
            if tline:
                lines.append(tline)
            vt = vol_trend(raw)
            if vt:
                lines.append(vt)
            lines.append("🔭 큰 틀: 일봉 " + TREND_TXT[tr["1D"]] + " · 4시간 "
                         + TREND_TXT[tr["4H"]] + " · 1시간 " + TREND_TXT[tr["1H"]])
            cl = counter_line(bad, "short")
            if cl:
                lines.append(cl)
            lines.append(classify_short(tr))
            cap = NL.join(lines)
            png = make_chart(raw, sym, bar, "/tmp/short_" + str(count) + ".png")
            sent_ok = send_photo(token, chat, png, cap) if png else False
            if not sent_ok:
                sent_ok = send_text(token, chat, cap)
            if sent_ok:
                sent[key] = now
                count += 1
                time.sleep(1)

    keep = {}
    for k, v in sent.items():
        try:
            if int(v) > now - 2 * 86400000:
                keep[k] = v
        except Exception:
            pass
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(keep, open(STATE, "w", encoding="utf-8"))
    print("숏 알림 " + str(count) + "건 보냄")


if __name__ == "__main__":
    main()
