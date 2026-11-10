# 台指夜盤三數字決策

每個交易日從期交所抓「夜盤漲跌、夜盤量占比、外資夜盤多空淨額」，判斷當天屬於四種劇本中的哪一種，附近期回測。

- 雲端：GitHub Actions 定時執行 `taifex_night.py`，部署到 GitHub Pages
- 本機：`python taifex_night.py --open`

方法出自小王子影片；資料來源：臺灣期貨交易所。非投資建議。
