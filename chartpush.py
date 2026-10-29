import os, re, sys, time, runpy
import requests

HOSTS = ["https://www.okx.com", "https://aws.okx.com"]
NL = chr(10)
MAX_CHARTS = 10
CHART_BARS = 80
VWAP_N = 100
BAR_CODE = {"1분": "1m", "5분": "5m", "15분": "15m", "1시간": "1H",
            "4시간": "4H", "일봉": "1D", "주봉": "1W", "월봉": "1M"}
BAR_MS = {"1m": 60000, "5m": 300000, "15m": 900000, "1H": 3600000,
          "4H": 14400000, "1D": 86400000, "1W": 604800000, "1M": 2592000000}
TAGS = [("거가중 1분", "⚡"), ("거가중", "❕"), ("200선", "❗"),
        ("VWAP 돌파", "📈"), ("전고점", "🚀"), ("뚫은 직후", "🔔"),
        ("정배열 시작", "🟢"), ("역배열 시작", "🔴")]
CHK_VOL = 1.5
CHK_TURN = {"1m": 100000, "5m": 300000}
CHK_TURN_DEFAULT = 1000000
STOP_PCT = {"1m": 1.0, "5m": 1.5, "15m": 2.0}
STOP_DEFAULT = 3.0
STOP_BARS = 10
VOL_AVG = 20
RE_A = re.compile(r"^(1분|5분|15분|1시간|4시간|일봉|주봉|월봉) · (\S+)")
RE_B = re.compile(r"^(\S+) (일봉|1시간) .*고점 돌파")
RE_C = re.compile(r"^(\S+) 거가중 위")
_real_post = requests.post


def parse(text):
    items = []
    tag = ""
    for line in text.split(NL):
        s = line.strip()
        if not s:
            continue
        m = RE_A.match(s)
        if m:
            items.append((m.group(2), BAR_CODE[m.group(1)], (tag + " " + s).strip()))
            continue
        m = RE_B.match(s)
        if m:
            items.append((m.group(1), BAR_CODE[m.group(2)], (tag + " " + s).strip()))
            continue
        m = RE_C.match(s)
        if m:
            items.append((m.group(1), "1m", (tag + " " + s).strip()))
            continue
        if s.startswith("http") or s.startswith("(그 외"):
            continue
        for key, emo in TAGS:
            if key in s:
                tag = emo
                break
    return items


def get_candles(inst, bar):
    for host in HOSTS:
        try:
            r = requests.get(host + "/api/v5/market/candles",
                             params={"instId": inst, "bar": bar, "limit": "300"},
                             timeout=10, headers={"User-Agent": "Mozilla/5.0"})
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


def rsi6(closes):
    n = 6
    if len(closes) < n + 2:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    out = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(gains) + 1):
        ag = (ag * (n - 1) + gains[i - 1]) / n
        al = (al * (n - 1) + losses[i - 1]) / n
        out = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def fmt_usdt(v):
    if v >= 100000000:
        return format(v / 100000000, ".1f") + "억"
    if v >= 10000:
        return format(v / 10000, ",.0f") + "만"
    return format(v, ",.0f")


def verdict(raw, bar):
    try:
        cs = list(reversed(raw))
        n = len(cs)
        if n < 40:
            return None
        closes = [float(x[4]) for x in cs]
        why = []
        r = rsi6(closes)
        if r is not None and r <= 30:
            why.append("RSI6 " + format(r, ".0f") + "(30 이하)")
        e7 = ema_full(closes, 7)
        if e7[-1] is not None and closes[-1] <= e7[-1]:
            why.append("EMA7 아래")
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
        cur_turn = float(cs[ki][7]) * scale
        past = [float(x[5]) for x in cs[max(0, ki - VOL_AVG):ki]]
        avg = sum(past) / len(past) if past else 0
        vx = cur_vol / avg if avg > 0 else 0
        if vx < CHK_VOL:
            why.append("거래량 ×" + format(vx, ".1f"))
        need = CHK_TURN.get(bar, CHK_TURN_DEFAULT)
        if cur_turn < need:
            why.append("거래대금 " + fmt_usdt(cur_turn))
        price = closes[-1]
        low_n = min(float(x[3]) for x in cs[max(0, ki - STOP_BARS):ki])
        pct = (low_n / price - 1) * 100
        lim = STOP_PCT.get(bar, STOP_DEFAULT)
        if low_n >= price:
            why.append("손절 계산 불가")
        elif pct <= -lim:
            why.append("손절폭 " + format(pct, ".1f") + "%")
        if why:
            return "🧭 ❌ 패스: " + " · ".join(why)
        return ("🧭 ✅ 진입 후보 (거래량 ×" + format(vx, ".1f")
                + " · 손절 " + format(pct, ".1f") + "%)")
    except Exception as e:
        print("판정 오류:", type(e).__name__)
        return None


def make_chart(raw, sym, bar, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.gridspec import GridSpec

        cs = list(reversed(raw))
        n = len(cs)
        if n < 30:
            return None
        closes = [float(x[4]) for x in cs]
        idx = list(range(max(0, n - CHART_BARS), n))
        fig = plt.figure(figsize=(8, 6), facecolor="#0b0e11")
        gs = GridSpec(2, 1, height_ratios=[4, 1], hspace=0.08)
        ax1 = fig.add_subplot(gs[0])
        ax2 = fig.add_subplot(gs[1], sharex=ax1)
        for ax in (ax1, ax2):
            ax.set_facecolor("#0b0e11")
            ax.tick_params(colors="#9aa0a6", labelsize=8)
            for sp in ax.spines.values():
                sp.set_color("#2a2e35")
            ax.grid(color="#1e2229", lw=0.5)
        plt.setp(ax1.get_xticklabels(), visible=False)

        lo_all, hi_all = None, None
        for i in idx:
            o, h, l, c = (float(cs[i][1]), float(cs[i][2]),
                          float(cs[i][3]), float(cs[i][4]))
            col = "#26a69a" if c >= o else "#ef5350"
            ax1.plot([i, i], [l, h], color=col, lw=1)
            body = max(abs(c - o), (h - l) * 0.02, 1e-12)
            ax1.bar(i, body, bottom=min(o, c), width=0.7, color=col)
            ax2.bar(i, float(cs[i][5]), width=0.7, color=col)
            lo_all = l if lo_all is None else min(lo_all, l)
            hi_all = h if hi_all is None else max(hi_all, h)

        for k, colr in ((7, "#c792ea"), (20, "#ffd54f"), (50, "#66bb6a"), (200, "#ef5350")):
            e = ema_full(closes, k)
            xs = [i for i in idx if e[i] is not None]
            if xs:
                ax1.plot(xs, [e[i] for i in xs], color=colr, lw=1.3, label="EMA" + str(k))

        vx, vy = [], []
        for i in idx:
            if i < VWAP_N - 1:
                continue
            w = cs[i - VWAP_N + 1:i + 1]
            v = sum(float(x[5]) for x in w)
            if v > 0:
                pv = sum(((float(x[2]) + float(x[3]) + float(x[4])) / 3) * float(x[5]) for x in w)
                vx.append(i)
                vy.append(pv / v)
        if vx:
            ax1.plot(vx, vy, color="#ffffff", lw=1.6, label="VWAP100")

        ax1.legend(loc="upper left", fontsize=7, facecolor="#0b0e11",
                   edgecolor="#2a2e35", labelcolor="#cfd3d8", ncol=5)
        if lo_all is not None and hi_all > lo_all:
            m = (hi_all - lo_all) * 0.06
            ax1.set_ylim(lo_all - m, hi_all + m)
        fig.suptitle(sym + "  " + bar, color="#e8eaed", fontsize=12, y=0.95)
        fig.savefig(path, dpi=100, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception as e:
        print("차트 그리기 오류:", type(e).__name__)
        return None


def handle(url, data):
    text = data.get("text", "")
    items = parse(text)
    if not items:
        return None
    chat = data.get("chat_id")
    photo_url = url[:-len("sendMessage")] + "sendPhoto"
    last = None
    rest = []
    for n, (sym, bar, cap) in enumerate(items):
        if n >= MAX_CHARTS:
            rest.append(cap)
            continue
        png = None
        raw = get_candles(sym + "-USDT-SWAP", bar)
        time.sleep(0.2)
        if raw:
            png = make_chart(raw, sym, bar, "/tmp/cp_" + str(n) + ".png")
        if png:
            full_cap = cap
            if "역배열" not in cap:
                v = verdict(raw, bar)
                if v:
                    full_cap = cap + NL + v
            try:
                with open(png, "rb") as f:
                    r = _real_post(photo_url, data={"chat_id": chat, "caption": full_cap[:1000]},
                                   files={"photo": f}, timeout=30)
                if r.status_code == 200:
                    last = r
                    time.sleep(1)
                    continue
            except Exception:
                pass
        rest.append(cap)
    if last is None:
        return None
    if rest:
        _real_post(url, data={"chat_id": chat, "text": NL.join(rest)}, timeout=15)
    return last


def patched_post(url, *a, **kw):
    try:
        if "api.telegram.org" in url and url.endswith("/sendMessage"):
            res = handle(url, kw.get("data") or {})
            if res is not None:
                return res
    except Exception as e:
        print("사진 변환 오류:", type(e).__name__)
    return _real_post(url, *a, **kw)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, sys.argv[1])
    sys.argv = sys.argv[1:]
    sys.path.insert(0, here)
    requests.post = patched_post
    runpy.run_path(target, run_name="__main__")
