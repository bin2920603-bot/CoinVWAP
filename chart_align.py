import os, json, time, requests

HOSTS = ["https://www.okx.com", "https://aws.okx.com"]
NL = chr(10)
TOP_N = 100
LOOKBACK = 2
MAX_ALERTS = 12
BARS = [("5m", "5분"), ("15m", "15분")]
ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "data", "align.json")


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


def ema_full(values, n):
    if len(values) < n + 1:
        return [None] * len(values)
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    out = [None] * (n - 1) + [e]
    for v in values[n:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


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

    bulls, bears, keys = [], [], []
    count = 0
    for vol, inst in rows[:TOP_N]:
        if count >= MAX_ALERTS:
            break
        name = inst.replace("-USDT-SWAP", "")
        for bar, label in BARS:
            if count >= MAX_ALERTS:
                break
            time.sleep(0.15)
            c = get("/api/v5/market/candles", {"instId": inst, "bar": bar, "limit": "300"})
            if not c:
                continue
            cs = [x for x in c if x[8] == "1"][::-1]
            if len(cs) < 210:
                continue
            closes = [float(x[4]) for x in cs]
            e7 = ema_full(closes, 7)
            e20 = ema_full(closes, 20)
            e50 = ema_full(closes, 50)
            e200 = ema_full(closes, 200)
            n = len(cs)
            state = [0] * n
            for i in range(n):
                if e200[i] is None:
                    continue
                if e7[i] > e20[i] > e50[i] > e200[i]:
                    state[i] = 1
                elif e7[i] < e20[i] < e50[i] < e200[i]:
                    state[i] = -1
            hit = None
            for i in range(n - 1, n - 1 - LOOKBACK, -1):
                if i < 1 or e200[i - 1] is None:
                    continue
                if state[i] != 0 and state[i] != state[i - 1]:
                    hit = i
                    break
            if hit is None:
                continue
            kind = "bull" if state[hit] == 1 else "bear"
            key = inst + "|" + bar + "|" + kind
            if sent.get(key) == cs[hit][0]:
                continue
            line = label + " · " + name + " " + ("정배열 시작" if kind == "bull" else "역배열 시작")
            (bulls if kind == "bull" else bears).append(line)
            keys.append((key, cs[hit][0]))
            count += 1

    parts = []
    if bulls:
        parts.append("🟢 정배열 시작" + NL + NL.join(bulls))
    if bears:
        parts.append("🔴 역배열 시작" + NL + NL.join(bears))
    if parts:
        r = requests.post("https://api.telegram.org/bot" + token + "/sendMessage",
                          data={"chat_id": chat, "text": (NL + NL).join(parts)}, timeout=30)
        if r.status_code == 200:
            for k, v in keys:
                sent[k] = v
            print("정배열/역배열 알림 " + str(len(keys)) + "건 보냄")
        else:
            print("전송 실패:", r.status_code)
    else:
        print("정배열/역배열: 새로 알릴 것 없음")

    keep = {}
    for k, v in sent.items():
        try:
            if int(v) > now - 2 * 86400000:
                keep[k] = v
        except Exception:
            pass
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(keep, open(STATE, "w", encoding="utf-8"))


if __name__ == "__main__":
    main()
