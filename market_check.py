# SOX・SOXL の市場データだけを計算して、latest.txt / latest.json / history.csv に書く。
# GitHub Actions が毎朝（日本時間 6:35 と 7:35）に実行する。個人の予算・約定はここに置かない。
import datetime as dt, json, os, sys, time, urllib.request

SIGNAL_START = "2026-11-04"   # この日の終値から ①② の判定を始める
HERE = os.path.dirname(os.path.abspath(__file__))


def get(sym, rng):
    s = sym.replace("^", "%5E").replace("=", "%3D"); err = None
    for n in range(4):
        for host in ("query1", "query2"):
            try:
                u = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=1d" % (host, s, rng)
                r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))["chart"]["result"][0]
                q = r["indicators"]["quote"][0]; off = r["meta"].get("gmtoffset", 0) or 0
                return [(dt.datetime.utcfromtimestamp(t + off).date(), h, l, c, v)
                        for t, h, l, c, v in zip(r["timestamp"], q["high"], q["low"], q["close"], q["volume"]) if c is not None]
            except Exception as e:
                err = e; time.sleep(2 * (n + 1))
    raise err


def rsi(c, n=14):
    d = [c[i] - c[i - 1] for i in range(1, len(c))]
    ag = sum(max(x, 0) for x in d[:n]) / n; al = sum(-min(x, 0) for x in d[:n]) / n
    for x in d[n:]:
        ag = (ag * (n - 1) + max(x, 0)) / n; al = (al * (n - 1) - min(x, 0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def main():
    now = dt.datetime.utcnow(); jst = now + dt.timedelta(hours=9)
    start = dt.date.fromisoformat(SIGNAL_START)
    sox = get("^SOX", "2y"); dates = [r[0] for r in sox]; c = [r[3] for r in sox]
    s200 = [None] * len(c)
    for i in range(199, len(c)):
        s200[i] = sum(c[i - 199:i + 1]) / 200.0
    above = [s200[i] is not None and c[i] > s200[i] for i in range(len(c))]
    i = len(c) - 1; d0 = dates[i]
    k = 0
    while above[i - k]:
        k += 1
    hi = {dates[j]: max(c[max(0, j - 251):j + 1]) for j in range(len(c))}
    ctx = {dates[j]: s200[j] is not None and c[j] < s200[j] and c[j] / hi[dates[j]] - 1 <= -0.15 for j in range(len(c))}
    sig1 = next((dates[j] for j in range(len(c)) if dates[j] >= start and above[j]), None)
    sig2 = next((dates[j] for j in range(1, len(c)) if dates[j] >= start and above[j] and above[j - 1]), None)
    soxl = get("SOXL", "6mo")
    J = {
        "generated_utc": now.strftime("%Y-%m-%d %H:%M"), "generated_jst": jst.strftime("%Y-%m-%d %H:%M"),
        "us_date": str(d0),
        "sox": {"close": round(c[i], 2), "chg": round(c[i] / c[i - 1] - 1, 4), "ma200": round(s200[i], 2), "gap": round(c[i] / s200[i] - 1, 4),
                "next_close_threshold": round(sum(c[i - 198:i + 1]) / 199.0, 1), "days_above": k,
                "ma20_gap": round(c[i] / (sum(c[-20:]) / 20) - 1, 4), "ma50_gap": round(c[i] / (sum(c[-50:]) / 50) - 1, 4),
                "rsi14": round(rsi(c[-120:]), 1), "from_52w_high": round(c[i] / hi[d0] - 1, 4)},
        "signals": {"start_date": SIGNAL_START, "sig1_date": str(sig1) if sig1 else None, "sig2_date": str(sig2) if sig2 else None},
        "soxl": {"date": str(soxl[-1][0]), "close": round(soxl[-1][3], 2), "chg": round(soxl[-1][3] / soxl[-2][3] - 1, 4)},
    }
    # 出来高（セリングクライマックス）。検証は2001年〜のSOXX。局面はSOXが200日線の下かつ52週高値から-15%以下
    try:
        R = get("SOXX", "1y")

        def day(j):
            d, h, l, cc, v = R[j]; pc = R[j - 1][3]; avg = sum(R[m][4] or 0 for m in range(j - 50, j)) / 50.0
            vr = (v or 0) / avg if avg else 0; r = cc / pc - 1; lo = l / pc - 1; pos = (cc - l) / (h - l) if h > l else 0.5
            cx = ctx.get(d, False)
            return dict(date=str(d), ratio=round(vr, 2), chg=round(r, 4), close_pos=round(pos, 2),
                        A=bool(cx and r <= -0.03 and vr >= 2), B=bool(cx and vr >= 2 and lo <= -0.03 and pos >= 0.5), C=bool(cx and r < 0 and vr >= 3))
        t = day(len(R) - 1); y = day(len(R) - 2)
        t["E"] = bool(y["A"] and t["chg"] > 0); t["A_then_down"] = bool(y["A"] and t["chg"] <= 0)
        try:
            M = get("SMH", "1y")
            t["smh_ratio"] = round((M[-1][4] or 0) / (sum(M[m][4] or 0 for m in range(len(M) - 51, len(M) - 1)) / 50.0), 2)
        except Exception:
            t["smh_ratio"] = None
        J["volume"] = t
    except Exception as e:
        J["volume"] = {"error": str(e)}
    J["macro"] = {}; J["semis"] = {}
    for s, lab, grp in (("^VIX", "VIX", "macro"), ("^VIX3M", "VIX3M", "macro"), ("^TNX", "US10Y", "macro"), ("BZ=F", "Brent", "macro"),
                        ("CL=F", "WTI", "macro"), ("HYG", "HYG", "macro"), ("NVDA", "NVDA", "semis"), ("AMD", "AMD", "semis"),
                        ("MU", "MU", "semis"), ("AVGO", "AVGO", "semis"), ("TSM", "TSM", "semis"), ("SNDK", "SNDK", "semis")):
        try:
            r = get(s, "1mo"); J[grp][lab] = {"close": round(r[-1][3], 3), "chg": round(r[-1][3] / r[-2][3] - 1, 4), "date": str(r[-1][0])}
        except Exception:
            J[grp][lab] = None

    # 人が読む版
    S = J["sox"]; V = J["volume"]; m = J["macro"]
    L = ["SOX・SOXL 市場データ（米国 %s 終値／作成 %s JST）" % (d0, J["generated_jst"]),
         "SOX %.1f（前日比 %+.2f%%）／200日線 %.1f（差 %+.2f%%）／連続で上：%d日" % (S["close"], S["chg"] * 100, S["ma200"], S["gap"] * 100, k),
         "  次の終値で200日線の上に出る水準：SOX %.0f 超（今より %+.2f%%）" % (S["next_close_threshold"], (S["next_close_threshold"] / S["close"] - 1) * 100),
         "  20日線 %+.1f%%・50日線 %+.1f%%・RSI14 %.0f・52週高値から %+.1f%%" % (S["ma20_gap"] * 100, S["ma50_gap"] * 100, S["rsi14"], S["from_52w_high"] * 100),
         "  ①の合図（%s以降で初めて200日線の上で引けた日）：%s／②の合図（2日連続）：%s" % (
             SIGNAL_START, J["signals"]["sig1_date"] or "まだ", J["signals"]["sig2_date"] or "まだ"),
         "SOXL $%.2f（前日比 %+.1f%%）" % (J["soxl"]["close"], J["soxl"]["chg"] * 100)]
    if "error" in V:
        L.append("出来高：取得失敗（%s）" % V["error"])
    else:
        sig = [n for key, n in (("B", "B 大商いの下ヒゲ"), ("C", "C 出来高3倍の下げ"), ("E", "E 大商い下げの翌日に反発")) if V[key]]
        note = "（今日はA 大商いの下げ。単独では底の合図にならない）" if V["A"] else ("（前日のA 大商いの下げに続き今日も下げ）" if V["A_then_down"] else "")
        L.append("出来高（SOXX）：50日平均の%.2f倍%s・前日比 %+.2f%%・引けは日中値幅の%.0f%%の位置" % (
            V["ratio"], "（SMH %.2f倍）" % V["smh_ratio"] if V.get("smh_ratio") else "", V["chg"] * 100, V["close_pos"] * 100))
        L.append("出来高の合図：%s%s" % ("／".join(sig) if sig else "なし", note))

    def f(g, lab, fmt):
        x = J[g].get(lab)
        return "%s 取得失敗" % lab if not x else fmt % (x["close"], x["chg"] * 100)
    vv = "・VIX/VIX3M %.2f" % (m["VIX"]["close"] / m["VIX3M"]["close"]) if m.get("VIX") and m.get("VIX3M") else ""
    L.append("数値：" + f("macro", "VIX", "VIX %.1f（%+.0f%%）") + vv + "・" + f("macro", "US10Y", "米10年債 %.2f%%（%+.1f%%）") + "・" +
             f("macro", "Brent", "Brent $%.1f（%+.1f%%）") + "・" + f("macro", "WTI", "WTI $%.1f（%+.1f%%）") + "・" + f("macro", "HYG", "HYG %.2f（%+.1f%%）"))
    L.append("主な半導体：" + "・".join(f("semis", l, l + " $%.2f（%+.1f%%）") for l in ("NVDA", "AMD", "MU", "AVGO", "TSM", "SNDK")))

    with open(os.path.join(HERE, "latest.json"), "w", encoding="utf-8") as fo:
        json.dump(J, fo, ensure_ascii=False, indent=1)
    with open(os.path.join(HERE, "latest.txt"), "w", encoding="utf-8") as fo:
        fo.write("\n".join(L) + "\n")
    hp = os.path.join(HERE, "history.csv")
    old = open(hp, encoding="utf-8").read() if os.path.exists(hp) else "us_date,sox,ma200,gap,days_above,soxl,soxx_vol_ratio,vol_flags\n"
    if ("\n%s," % d0) not in old:
        flags = "" if "error" in V else "".join(x for x in "ABCE" if V[x])
        old += "%s,%.2f,%.2f,%.4f,%d,%.2f,%s,%s\n" % (d0, S["close"], S["ma200"], S["gap"], k, J["soxl"]["close"], "" if "error" in V else V["ratio"], flags)
        with open(hp, "w", encoding="utf-8") as fo:
            fo.write(old)
    sys.stdout.buffer.write(("\n".join(L) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
