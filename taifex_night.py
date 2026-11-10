"""台指夜盤三數字決策儀表板

來源影片：小王子〈三個數字判斷隔天台股〉
  1. 夜盤漲跌點數    → 開盤方向（|漲跌| ≥ 300 點才算明顯）
  2. 夜盤量占比      → 可信度：夜盤量 / (前一日日盤量 + 夜盤量)；<30% 低、≥40% 強
  3. 外資夜盤多空淨額 → 外資站哪邊（|淨額| ≥ 1000 口才算明確）
  夜盤方向 × 外資方向 → 四種劇本

資料：台灣期貨交易所 www.taifex.com.tw（期貨每日交易行情、三大法人盤後）
期交所把「D-1 晚上 15:00 → D 早上 05:00」的夜盤記在交易日 D。

用法：python taifex_night.py           → 抓資料、產生 index.html
      python taifex_night.py --open    → 產生後用瀏覽器打開
"""
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta, timezone

TW = timezone(timedelta(hours=8))  # 雲端主機是 UTC，一律用台灣時間
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_HTML = HERE / "index.html"
CACHE = HERE / "cache.json"
LOG = HERE / "update.log"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) taifex-night-dashboard"
FUT_URL = "https://www.taifex.com.tw/cht/3/futDataDown"
INST_AH_URL = "https://www.taifex.com.tw/cht/3/futContractsDateAhDown"

HISTORY_DAYS = 120          # 回測抓多少天
CHG_TH = 300                # 夜盤漲跌門檻（點）
RATIO_LOW, RATIO_HIGH = 0.30, 0.40
FOREIGN_TH = 1000           # 外資淨額門檻（口）

SCENARIOS = {
    1: dict(name="劇本一：漲勢扎實", cond="夜盤漲 ＋ 外資淨多",
            open="開高", trend="續漲", tone="up",
            advice="開盤容易漲，且可能一路往上。順勢偏多，不急著追高可等回檔。"),
    2: dict(name="劇本二：開高走低", cond="夜盤漲 ＋ 外資淨空",
            open="開高", trend="走低", tone="warn",
            advice="價格在漲但外資沒站過去，常見開高後力道不足殺下來。別追開盤的高。"),
    3: dict(name="劇本三：跌勢未止", cond="夜盤跌 ＋ 外資淨空",
            open="開低", trend="續跌", tone="down",
            advice="價格跌、外資也看空。不要開低就去抄底，容易越接越低。"),
    4: dict(name="劇本四：開低反彈", cond="夜盤跌 ＋ 外資淨多",
            open="開低", trend="反彈", tone="rebound",
            advice="開低但外資沒跟著殺，開低反彈機率較高。要抄底選這種。"),
}


def log(msg: str) -> None:
    line = f"[{datetime.now(TW):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def post_csv(url: str, params: dict) -> list[list[str]]:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    text = raw.decode("big5", errors="replace")
    if text.lstrip().startswith("<"):
        raise RuntimeError(f"期交所回傳 HTML 而非 CSV（{url}）")
    return [row for row in csv.reader(io.StringIO(text)) if row]


def num(s: str) -> float | None:
    s = (s or "").strip().replace(",", "").replace("%", "")
    if s in ("", "-", "NULL"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_futures(start: date, end: date) -> dict:
    """回傳 {日期: {'一般': row, '盤後': row}}，只取近月（單一月份中成交量最大者）。"""
    best: dict[tuple[str, str], dict] = {}
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=27), end)
        rows = post_csv(FUT_URL, {
            "down_type": "1", "commodity_id": "TX", "commodity_id2": "",
            "queryStartDate": cur.strftime("%Y/%m/%d"),
            "queryEndDate": chunk_end.strftime("%Y/%m/%d"),
        })
        for r in rows[1:]:
            if len(r) < 18 or r[1].strip() != "TX" or "/" in r[2]:
                continue
            d, session = r[0].strip(), r[17].strip()
            vol = num(r[9]) or 0
            rec = dict(month=r[2].strip(), open=num(r[3]), high=num(r[4]), low=num(r[5]),
                       close=num(r[6]), chg=num(r[7]), vol=vol, settle=num(r[10]))
            key = (d, session)
            if key not in best or vol > best[key]["vol"]:
                best[key] = rec
        cur = chunk_end + timedelta(days=1)
    out: dict[str, dict] = {}
    for (d, session), rec in best.items():
        out.setdefault(d, {})[session] = rec
    return out


def fetch_foreign_ah(start: date, end: date) -> dict:
    """回傳 {日期: 外資夜盤臺股期貨多空交易口數淨額}。"""
    out = {}
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=89), end)
        rows = post_csv(INST_AH_URL, {
            "queryStartDate": cur.strftime("%Y/%m/%d"),
            "queryEndDate": chunk_end.strftime("%Y/%m/%d"),
            "commodityId": "TXF",
        })
        for r in rows[1:]:
            if len(r) >= 8 and "外資" in r[2]:
                v = num(r[7])
                if v is not None:
                    out[r[0].strip()] = int(v)
        cur = chunk_end + timedelta(days=1)
    return out


def analyse(fut: dict, foreign: dict) -> list[dict]:
    dates = sorted(fut)
    rows = []
    for i, d in enumerate(dates):
        night = fut[d].get("盤後")
        if not night or night["chg"] is None or i == 0:
            continue
        prev_day = fut[dates[i - 1]].get("一般")
        if not prev_day:
            continue
        today = fut[d].get("一般")  # 當天日盤（收盤後才有）
        chg = night["chg"]
        ratio = night["vol"] / (prev_day["vol"] + night["vol"]) if (prev_day["vol"] + night["vol"]) else None
        fnet = foreign.get(d)

        scen = None
        if fnet is not None and chg != 0 and fnet != 0:
            scen = (1 if fnet > 0 else 2) if chg > 0 else (4 if fnet > 0 else 3)

        sig_chg = abs(chg) >= CHG_TH
        sig_ratio = ratio is not None and ratio >= RATIO_LOW
        sig_foreign = fnet is not None and abs(fnet) >= FOREIGN_TH
        strength = sum([sig_chg, sig_ratio, sig_foreign])

        res = None
        if today and today["open"] is not None and today["close"] is not None:
            base = prev_day["settle"] or prev_day["close"]
            gap = today["open"] - base
            intraday = today["close"] - today["open"]
            open_hit = (gap > 0) == (chg > 0) if gap != 0 else None
            trend_hit = None
            if scen and intraday != 0:
                want_up = scen in (1, 4)
                trend_hit = (intraday > 0) == want_up
            res = dict(open=today["open"], close=today["close"], high=today["high"], low=today["low"],
                       gap=gap, intraday=intraday, open_hit=open_hit, trend_hit=trend_hit)

        rows.append(dict(
            date=d, prev_date=dates[i - 1], month=night["month"],
            night_open=night["open"], night_close=night["close"], night_high=night["high"],
            night_low=night["low"], chg=chg, night_vol=int(night["vol"]), prev_vol=int(prev_day["vol"]),
            ratio=ratio, fnet=fnet, scen=scen, sig_chg=sig_chg, sig_ratio=sig_ratio,
            sig_foreign=sig_foreign, strength=strength, res=res,
            ratio_level=None if ratio is None else ("強" if ratio >= RATIO_HIGH else "中" if ratio >= RATIO_LOW else "低"),
        ))
    return rows


def stats(rows: list[dict]) -> dict:
    def summarize(sub):
        done = [r for r in sub if r["res"]]
        oh = [r for r in done if r["res"]["open_hit"] is not None]
        th = [r for r in done if r["res"]["trend_hit"] is not None]
        return dict(n=len(done),
                    open_hit=sum(r["res"]["open_hit"] for r in oh), open_n=len(oh),
                    trend_hit=sum(r["res"]["trend_hit"] for r in th), trend_n=len(th))
    by = {k: summarize([r for r in rows if r["scen"] == k]) for k in SCENARIOS}
    strong = {k: summarize([r for r in rows if r["scen"] == k and r["strength"] == 3]) for k in SCENARIOS}
    big = summarize([r for r in rows if r["sig_chg"]])
    allr = summarize(rows)
    return dict(by=by, strong=strong, big=big, all=allr)


# ---------------------------------------------------------------- HTML
def fmt_int(v, sign=False):
    if v is None:
        return "—"
    return f"{int(v):+,}" if sign else f"{int(v):,}"


def pct(a, b):
    return "—" if not b else f"{a / b * 100:.0f}%"


def render(rows: list[dict], st: dict, fetched_at: datetime) -> str:
    latest = rows[-1]
    s = SCENARIOS.get(latest["scen"])
    tone = s["tone"] if s else "flat"
    d_obj = datetime.strptime(latest["date"], "%Y/%m/%d")
    wd = "一二三四五六日"[d_obj.weekday()]
    prev_obj = datetime.strptime(latest["prev_date"], "%Y/%m/%d")

    def check(ok, text_ok, text_no):
        cls = "ok" if ok else "no"
        mark = "✓" if ok else "✗"
        return f'<span class="chk {cls}">{mark} {text_ok if ok else text_no}</span>'

    strength_txt = {3: "三項都達標：訊號強", 2: "兩項達標：訊號中等", 1: "只有一項達標：參考就好", 0: "都沒達標：訊號弱"}[latest["strength"]]

    ratio_pct = "—" if latest["ratio"] is None else f'{latest["ratio"] * 100:.1f}%'

    result_block = ""
    if latest["res"]:
        r = latest["res"]
        verdict = []
        if r["open_hit"] is not None:
            verdict.append("開盤方向 " + ("✓ 符合" if r["open_hit"] else "✗ 不符"))
        if r["trend_hit"] is not None:
            verdict.append("盤中走勢 " + ("✓ 符合" if r["trend_hit"] else "✗ 不符"))
        result_block = f'''
      <div class="result">
        <div class="result-h">當天日盤實際結果（已收盤）</div>
        <div class="result-row">開 {fmt_int(r["open"])}（跳空 {fmt_int(r["gap"], True)}）→ 收 {fmt_int(r["close"])}（盤中 {fmt_int(r["intraday"], True)}）</div>
        <div class="result-row">{"　".join(verdict)}</div>
        <div class="muted small">下一個夜盤（今晚 15:00 → 明早 05:00）的數字，明早約 06:00 後自動更新。</div>
      </div>'''

    # 回測表
    bt_rows = []
    for k, sc in SCENARIOS.items():
        a, b = st["by"][k], st["strong"][k]
        bt_rows.append(f'''<tr class="t-{sc["tone"]}"><td><b>{sc["name"]}</b><div class="muted small">{sc["cond"]}</div></td>
          <td class="n">{a["n"]}</td><td class="n">{pct(a["open_hit"], a["open_n"])}</td><td class="n">{pct(a["trend_hit"], a["trend_n"])}</td>
          <td class="n">{b["n"]}</td><td class="n">{pct(b["trend_hit"], b["trend_n"])}</td></tr>''')

    # 歷史明細
    hist = []
    for r in reversed(rows[-40:]):
        sc = SCENARIOS.get(r["scen"])
        res = r["res"]
        def hit(v):
            return "" if v is None else ('<span class="ok">✓</span>' if v else '<span class="no">✗</span>')
        hist.append(f'''<tr>
          <td>{r["date"][5:]}</td>
          <td class="n {'up' if r['chg'] > 0 else 'down'}{' b' if r['sig_chg'] else ''}">{fmt_int(r["chg"], True)}</td>
          <td class="n{' b' if r['sig_ratio'] else ''}">{'—' if r['ratio'] is None else f"{r['ratio']*100:.0f}%"}</td>
          <td class="n {'up' if (r['fnet'] or 0) > 0 else 'down'}{' b' if r['sig_foreign'] else ''}">{fmt_int(r["fnet"], True)}</td>
          <td><span class="pill p-{sc['tone'] if sc else 'flat'}">{sc['name'][:3] if sc else '—'}</span> <span class="dots">{'●' * r['strength']}{'○' * (3 - r['strength'])}</span></td>
          <td class="n">{fmt_int(res['gap'], True) if res else '…'} {hit(res['open_hit']) if res else ''}</td>
          <td class="n">{fmt_int(res['intraday'], True) if res else '…'} {hit(res['trend_hit']) if res else ''}</td>
        </tr>''')

    big = st["big"]
    first_date = rows[0]["date"]

    return f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="1800">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="台指夜盤">
<meta name="theme-color" content="#0f1115">
<title>台指夜盤決策</title>
<style>
:root{{--bg:#0f1115;--card:#171a21;--line:#262b36;--fg:#e8eaf0;--muted:#8a92a6;
--up:#ff4d4f;--down:#22c55e;--warn:#f59e0b;--rebound:#38bdf8;--flat:#8a92a6}}
@media (prefers-color-scheme: light){{:root{{--bg:#f6f7f9;--card:#fff;--line:#e3e6ec;--fg:#151821;--muted:#667085}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 "Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif}}
.wrap{{max-width:960px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:20px;margin:0}} h2{{font-size:16px;margin:32px 0 10px}}
.top{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}}
.muted{{color:var(--muted)}} .small{{font-size:12.5px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}}
.hero{{margin-top:14px;border-left:6px solid var(--{tone})}}
.hero .label{{font-size:13px;color:var(--muted)}}
.hero .scen{{font-size:28px;font-weight:800;color:var(--{tone});margin:4px 0}}
.hero .advice{{font-size:16px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}}
@media (max-width:640px){{.grid{{grid-template-columns:1fr}}}}
.metric .k{{font-size:13px;color:var(--muted)}}
.metric .v{{font-size:30px;font-weight:800;font-variant-numeric:tabular-nums}}
.metric .d{{font-size:12.5px;color:var(--muted)}}
.up{{color:var(--up)}} .down{{color:var(--down)}}
.chk{{display:inline-block;font-size:12.5px;padding:2px 8px;border-radius:999px;margin-top:6px}}
.chk.ok{{background:color-mix(in srgb,var(--rebound) 18%,transparent)}}
.chk.no{{background:color-mix(in srgb,var(--flat) 18%,transparent);color:var(--muted)}}
span.ok{{color:var(--rebound)}} span.no{{color:var(--muted)}}
.strength{{margin-top:10px;font-size:13px}}
.result{{margin-top:14px;padding-top:12px;border-top:1px dashed var(--line)}}
.result-h{{font-size:13px;color:var(--muted)}}
.tbl-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}
th,td{{padding:8px 6px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{color:var(--muted);font-weight:500;font-size:12.5px}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}} td.b{{font-weight:800}}
.pill{{font-size:12px;padding:1px 7px;border-radius:999px;border:1px solid currentColor}}
.p-up{{color:var(--up)}} .p-down{{color:var(--down)}} .p-warn{{color:var(--warn)}} .p-rebound{{color:var(--rebound)}} .p-flat{{color:var(--flat)}}
.dots{{color:var(--warn);font-size:11px;letter-spacing:1px}}
.matrix{{display:grid;grid-template-columns:auto 1fr 1fr;gap:6px;font-size:13.5px}}
.matrix div{{padding:10px;border-radius:10px;background:var(--card);border:1px solid var(--line)}}
.matrix .h{{background:none;border:none;color:var(--muted);font-size:12.5px;display:flex;align-items:center}}
.matrix .on{{outline:2px solid var(--{tone})}}
ul{{padding-left:20px;margin:6px 0}}
</style></head><body><div class="wrap">

<div class="top">
  <h1>台指夜盤三數字決策</h1>
  <div class="muted small">資料更新：{fetched_at:%m/%d %H:%M}　資料源：期交所</div>
</div>

<div class="card hero">
  <div class="label">給 {d_obj:%m/%d}（{wd}）日盤用　·　夜盤 {prev_obj:%m/%d} 15:00 → {d_obj:%m/%d} 05:00　·　近月 {latest["month"]}</div>
  <div class="scen">{s["name"] if s else "無明確劇本"}</div>
  <div class="advice">{s["advice"] if s else "夜盤或外資淨額為 0，無法歸類。"}</div>
  <div class="strength">訊號強度 <span class="dots">{'●' * latest['strength']}{'○' * (3 - latest['strength'])}</span>　{strength_txt}</div>
  {result_block}
</div>

<div class="grid">
  <div class="card metric">
    <div class="k">① 夜盤漲跌</div>
    <div class="v {'up' if latest['chg'] > 0 else 'down'}">{fmt_int(latest["chg"], True)}</div>
    <div class="d">夜盤收 {fmt_int(latest["night_close"])}（高 {fmt_int(latest["night_high"])} / 低 {fmt_int(latest["night_low"])}）</div>
    {check(latest["sig_chg"], "超過 300 點：方向明顯", "未達 300 點：方向不明顯")}
  </div>
  <div class="card metric">
    <div class="k">② 夜盤量占比</div>
    <div class="v">{ratio_pct}</div>
    <div class="d">夜盤 {fmt_int(latest["night_vol"])} ÷（前日日盤 {fmt_int(latest["prev_vol"])} ＋ 夜盤）</div>
    {check(latest["sig_ratio"], "≥30%：可信" + ("，≥40% 強訊號" if (latest["ratio"] or 0) >= RATIO_HIGH else ""), "<30%：參考價值低")}
  </div>
  <div class="card metric">
    <div class="k">③ 外資夜盤多空淨額</div>
    <div class="v {'up' if (latest['fnet'] or 0) > 0 else 'down'}">{fmt_int(latest["fnet"], True)} <span class="small muted">口</span></div>
    <div class="d">臺股期貨・外資及陸資・盤後交易口數淨額</div>
    {check(latest["sig_foreign"], "超過 1000 口：態度明確", "不到 1000 口：態度不明確")}
  </div>
</div>

<h2>四種劇本</h2>
<div class="matrix">
  <div class="h"></div><div class="h">外資淨多（＋）</div><div class="h">外資淨空（−）</div>
  <div class="h">夜盤漲</div>
  <div class="{'on' if latest['scen'] == 1 else ''}"><b class="up">劇本一 漲勢扎實</b><br>開高、可能續漲</div>
  <div class="{'on' if latest['scen'] == 2 else ''}"><b style="color:var(--warn)">劇本二 開高走低</b><br>開高但力道弱，易殺下來</div>
  <div class="h">夜盤跌</div>
  <div class="{'on' if latest['scen'] == 4 else ''}"><b style="color:var(--rebound)">劇本四 開低反彈</b><br>要抄底選這種</div>
  <div class="{'on' if latest['scen'] == 3 else ''}"><b class="down">劇本三 跌勢未止</b><br>別抄底，容易續跌</div>
</div>

<h2>回測：這套方法最近準不準？</h2>
<div class="muted small">期間 {first_date} 起共 {st["all"]["n"]} 個交易日。「開盤方向」= 日盤開盤相對前日結算價，是否和夜盤同方向；「劇本走勢」= 收盤相對開盤，是否符合劇本（一、四看收紅；二、三看收黑）。</div>
<div class="card tbl-wrap" style="margin-top:10px">
<table>
<tr><th>劇本</th><th class="n">次數</th><th class="n">開盤方向命中</th><th class="n">劇本走勢命中</th><th class="n">三項達標次數</th><th class="n">達標時走勢命中</th></tr>
{''.join(bt_rows)}
</table>
<div class="muted small" style="margin-top:8px">夜盤漲跌 ≥300 點的日子：開盤方向命中 {pct(big["open_hit"], big["open_n"])}（{big["open_n"]} 次）。樣本少的格子看看就好。</div>
</div>

<h2>最近 40 個交易日</h2>
<div class="card tbl-wrap">
<table>
<tr><th>交易日</th><th class="n">夜盤漲跌</th><th class="n">量占比</th><th class="n">外資淨額</th><th>劇本 / 強度</th><th class="n">開盤跳空</th><th class="n">收−開</th></tr>
{''.join(hist)}
</table>
<div class="muted small" style="margin-top:8px">粗體 = 該項達門檻（300 點 / 30% / 1000 口）。✓✗ = 是否符合預期。</div>
</div>

<h2>怎麼用</h2>
<div class="card small">
<ul>
<li>每個交易日早上 06:00 後（夜盤 05:00 收）打開這頁，看最上面的劇本卡。</li>
<li>先看 ① 決定開盤方向，再看 ② 確認可不可信，最後用 ③ 判斷能不能延續。</li>
<li>夜盤只能預告<b>開盤方向</b>，不保證收盤；劇本是機率，不是保證。搭配自己的停損。</li>
<li>方法出自〈小王子〉影片；數字每個交易日自動從期交所抓取計算。本頁不構成投資建議。</li>
</ul>
</div>

</div></body></html>'''


def main() -> int:
    today = datetime.now(TW).date()
    start = today - timedelta(days=HISTORY_DAYS)
    try:
        fut = fetch_futures(start, today)
        foreign = fetch_foreign_ah(start, today)
    except Exception as e:  # 網路錯誤：保留舊頁面
        log(f"抓取失敗：{e!r}")
        if CACHE.exists():
            log("沿用舊資料，頁面不更新")
        return 1
    rows = analyse(fut, foreign)
    if not rows:
        log("沒有可用資料")
        return 1
    st = stats(rows)
    now = datetime.now(TW)
    OUT_HTML.write_text(render(rows, st, now), encoding="utf-8")
    CACHE.write_text(json.dumps(dict(fetched_at=now.isoformat(), rows=rows), ensure_ascii=False, indent=1),
                     encoding="utf-8")
    L = rows[-1]
    log(f"更新完成：{L['date']} 夜盤 {L['chg']:+.0f}、量占比 {L['ratio'] or 0:.1%}、外資 {L['fnet']}、劇本 {L['scen']}")
    if "--open" in sys.argv:
        webbrowser.open(OUT_HTML.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
