import os, re, sys, time, runpy
import requests

HOSTS = ["https://www.okx.com", "https://aws.okx.com"]
NL = chr(10)
MAX_CHARTS = 10
CHART_BARS = 80
VWAP_N = 100
BAR_CODE = {"1분": "1m", "5분": "5m", "15분": "15m", "1시간": "1H",
            "4시간": "4H", "일봉": "1D", "주봉": "1W", "월봉": "1M"}
TAGS = [("거가중 1분", "⚡"), ("거가중", "❕"), ("200선", "❗"),
        ("VWAP 돌파", "📈"), ("전고점", "🚀"), ("뚫은 직후", "🔔")]
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
            try:
                with open(png, "rb") as f:
                    r = _real_post(photo_url, data={"chat_id": chat, "caption": cap[:1000]},
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
