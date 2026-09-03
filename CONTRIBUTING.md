# 開發協作

請先閱讀 [架構](docs/ARCHITECTURE.md)、[協定](docs/PROTOCOL.md) 與 [維護說明](docs/MAINTENANCE.md)。主要相容基準是特定日期的 Python 檔案，不是所有 C521M／APK 共用的推測協定。

- 使用 Python 3.12 虛擬環境與 `constraints.txt`，執行 README 的測試、Ruff 與 build。
- 協定變更先補 golden packet／狀態／loopback 測試；UI 需測多分頁與較窄畫面。
- 不新增 Node.js／Docker 或硬體需求，除非需求已明確變更。
- 不提交 `instance/`、虛擬環境、現場封包、卡號、帳密、APK、系統映像或反編譯產物。Issue 與截圖也需去識別化。
- 不使用外部服務作自動測試對端。測試限定 loopback 與臨時埠。
- 更新功能請同步維護繁中文件、`CHANGELOG.md` 與適用的實際畫面截圖。

尚未決定開源授權；建立公開 repository／授權／GitHub 遠端／推送前需由專案擁有者確認。本次交付不代為指定。
