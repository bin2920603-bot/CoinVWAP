import os, re, sys, json, time, runpy
from datetime import datetime, timedelta, timezone
import requests

HOSTS = ["https://www.okx.com", "https://aws.okx.com"]
NL = chr(10)
KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "data", "alert_log.json")
TARGET = 1.5
STOP = 1.5
WINDOW_H = 4
EXPIRE_H = 22
KEEP_DAYS = 8
REPORT_HOUR = 22
RE_A = re.compile(r"(1분|5분|15분|1시간|4시간|일봉|주봉|월봉) · (\S+)")
RE_B = re.compile(r"(\S+) (일봉|1시간) \d+일 고점 돌파")
RE_C = re.compile(r"(\S+) 거가중 위")
KINDS = [("RSI70", "RSI70 하향"), ("200선 저항", "200선 저항"),
         ("VWAP 아래로", "VWAP 아래로"), ("VWAP 아래에서", "흰느낌표"),
         ("VWAP 돌파", "VWAP 돌파"), ("고점 돌파", "전고점 돌파"),
         ("거가중 위", "거가중 1분"), ("200선 위", "200선"),
         ("양봉", "뚫은 직후")]
_orig_post = requests.post


def load():
    try:
        return json.load(open(LOG, encoding="utf-8"))
    except Exception:
        return {"items": [], "last_report": ""}


def save(d):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    json.dump(d, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


def okx_get(path, params):
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


def last_price(sym):
    d = okx_get("/api/v5/market/ticker", {"instId": sym + "-USDT-SWAP"})
    try:
        return float(d[0]["last"])
    except Exception:
        return None


def record(cap):
    lines = [s.strip() for s in cap.split(NL) if s.strip()]
    if not lines:
        return
    if "롱 타점" in lines[0]:
        direction = "long"
    elif "숏 타점" in lines[0]:
        direction = "short"
    else:
        return
    sym, bar, ev = None, None, ""
    for s in lines[1:]:
        m = RE_A.search(s)
        if m:
            bar, sym, ev = m.group(1), m.group(2), s
            break
        m = RE_B.search(s)
        if m:
            sym, bar, ev = m.group(1), m.group(2), s
            break
        m = RE_C.search(s)
        if m:
            sym, bar, ev = m.group(1), "1분", s
            break
    if not sym:
        return
    kind = "기타"
    for key, name in KINDS:
        if key in ev:
            kind = name
            break
    v = "none"
    for s in lines:
        if "🧭" in s:
            if "✅" in s:
                v = "ok"
            elif "❌" in s:
                v = "pass"
            break
    p0 = last_price(sym)
    if p0 is None:
        return
    data = load()
    data["items"].append({"t": int(time.time() * 1000), "sym": sym, "bar": bar,
                          "dir": direction, "kind": kind, "v": v, "p0": p0, "res": None})
    save(data)
    print("기록:", sym, bar, direction, kind, v)


def wrapped(url, *a, **kw):
    r = _orig_post(url, *a, **kw)
    try:
        if "api.telegram.org" in url and url.endswith("/sendPhoto") and r.status_code == 200:
            record((kw.get("data") or {}).get("caption", ""))
    except Exception as e:
        print("기록 오류:", type(e).__name__)
    return r


def evaluate(items, now):
    cache = {}
    for it in items:
        if it.get("res"):
            continue
        end = it["t"] + WINDOW_H * 3600000
        if now < end:
            continue
        if now > it["t"] + EXPIRE_H * 3600000:
            it["res"] = "skip"
            continue
        sym = it["sym"]
        if sym not in cache:
            cache[sym] = okx_get("/api/v5/market/candles",
                                 {"instId": sym + "-USDT-SWAP", "bar": "5m", "limit": "300"})
            time.sleep(0.15)
        raw = cache[sym]
        if not raw:
            continue
        cs = [c for c in reversed(raw) if it["t"] <= int(c[0]) < end]
        if not cs:
            continue
        p0 = it["p0"]
        res = "none"
        for c in cs:
            h, l = float(c[2]), float(c[3])
            if it["dir"] == "long":
                hit_stop = l <= p0 * (1 - STOP / 100)
                hit_tgt = h >= p0 * (1 + TARGET / 100)
            else:
                hit_stop = h >= p0 * (1 + STOP / 100)
                hit_tgt = l <= p0 * (1 - TARGET / 100)
            if hit_stop:
                res = "loss"
                break
            if hit_tgt:
                res = "win"
                break
        it["res"] = res


def row(title, its):
    w = sum(1 for i in its if i.get("res") == "win")
    l = sum(1 for i in its if i.get("res") == "loss")
    n = sum(1 for i in its if i.get("res") == "none")
    k = sum(1 for i in its if not i.get("res"))
    t = title + ": " + str(len(its)) + "건 → 성공 " + str(w) + " · 실패 " + str(l)
    if n:
        t += " · 무승부 " + str(n)
    if k:
        t += " · 대기 " + str(k)
    if w + l > 0:
        t += " (성공률 " + str(w * 100 // (w + l)) + "%)"
    return t


def build_report(items):
    items = [i for i in items if i.get("res") != "skip"]
    out = ["📊 알림 성적표 (최근 " + str(KEEP_DAYS) + "일)",
           "기준: 알림 가격에서 " + str(WINDOW_H) + "시간 안에 +" + str(TARGET)
           + "%(숏은 -" + str(TARGET) + "%)를 먼저 가면 성공, 반대로 " + str(STOP)
           + "%를 먼저 가면 실패 (같은 봉이면 실패)", ""]
    groups = [("🟢 롱 ✅후보", "long", "ok"), ("🟢 롱 ❌패스", "long", "pass"),
              ("🔻 숏 ✅후보", "short", "ok"), ("🔻 숏 ❌패스", "short", "pass")]
    for title, d, v in groups:
        its = [i for i in items if i["dir"] == d and i["v"] == v]
        out.append(row(title, its))
    out.append("")
    out.append("종류별 (✅후보만)")
    seen = []
    for i in items:
        key = (i["dir"], i["kind"])
        if i["v"] == "ok" and key not in seen:
            seen.append(key)
    for d, kind in seen:
        its = [i for i in items if i["dir"] == d and i["kind"] == kind and i["v"] == "ok"]
        out.append(row(("🟢 " if d == "long" else "🔻 ") + kind, its))
    out.append("")
    out.append("※ 건수가 적으면 믿지 마세요. 최소 30건은 모여야 의미가 있어요.")
    return NL.join(out)


def run_report():
    data = load()
    now = int(time.time() * 1000)
    evaluate(data["items"], now)
    cut = now - KEEP_DAYS * 86400000
    data["items"] = [i for i in data["items"] if i["t"] >= cut]
    nk = datetime.now(KST)
    today = nk.strftime("%Y-%m-%d")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token and chat and nk.hour == REPORT_HOUR and data.get("last_report") != today:
        try:
            r = requests.post("https://api.telegram.org/bot" + token + "/sendMessage",
                              data={"chat_id": chat, "text": build_report(data["items"])},
                              timeout=15)
            if r.status_code == 200:
                data["last_report"] = today
                print("성적표 보냄")
        except Exception as e:
            print("성적표 오류:", type(e).__name__)
    save(data)
    print("기록 " + str(len(data["items"])) + "건")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "report":
        run_report()
    elif len(sys.argv) >= 2:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, here)
        requests.post = wrapped
        target = os.path.join(here, sys.argv[1])
        sys.argv = sys.argv[1:]
        runpy.run_path(target, run_name="__main__")
