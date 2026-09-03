# 驗證紀錄

## 本次環境

- 日期：2026-09-03。
- Windows、Python 3.12.13，依賴版本見 `constraints.txt`。
- 真正 Waitress／Flask 服務與瀏覽器；網頁在 `0.0.0.0:5000`。
- TCP 僅連本機短暫 loopback fixture，使用系統分配的臨時埠及人工 UID。
- 初次本機驗證時未連現場主機、未操作真實復健機，也尚未推送 GitHub；後續依專案擁有者指示發布，遠端執行結果見 [GitHub Actions](https://github.com/root50643/rehab_device_simulator/actions/workflows/tests.yml)。

## 自動測試

`python -m pytest -q` 最終 **149 項通過**，連續重跑三次通過。涵蓋：

| 類別 | 驗證範圍 |
| --- | --- |
| 純協定 | 六種命令的固定封包、UID 前導零／不同長度、checksum、每個切點拆包、黏包、內含 7A／7F、最大 payload、垃圾資料與錯包後恢復 |
| 狀態機 | 開始／停止 FIFO 延遲、暫停保持追蹤、四種硬體狀態、三種狀態查詢、Level 0／65535、自動跟隨與覆寫、變值回報、實際最後送出值、故障通知 |
| 輸入與儲存 | 整數／布林／小數邊界、未知欄位、離線拒絕、JSON／MIME／大小限制、host／IDNA、版本衝突、原子存檔失敗、損毀設定不覆寫 |
| TCP | 真實 loopback ACK／拆黏包／EOF／重連／主機切換；拒絕連線、timeout 與取消連線的受控失敗；三次上限、手動斷線停止重試、控制命令不被 ACK 等待吞掉、舊卡號不重播、echo 防迴圈 |
| 事件與文件 | 1000 筆界線、cursor／epoch、清除／JSON 下載、狀態 snapshot 序號、文件連結與截圖資產 |

Ruff check、Ruff format check、JavaScript 語法檢查，以及 Python wheel／sdist 建置皆已執行。初次本機驗證未包含 Ubuntu；GitHub Actions 提供 Windows／Ubuntu + Python 3.12，跨平台結果須以對應 commit 的實際 Actions 執行紀錄為準，不可把已配置 CI 視為已通過。

## 瀏覽器操作

下列在真正運作的網頁執行，不是只用 API 推測 UI：

- 初始未連線，傳送按鈕停用；修改／儲存 socket_server、port、自動重連並建立測試連線。
- 非法 UID `GG` 被拒絕；人工 UID `01234567` 保留前導零，看到真實 TCP 卡號 ACK、開始及 Level 指令。
- RPM 64、BPM 92、Level 10；數字欄位離焦提交，兩分頁同步，變值產生正確 `23` 封包；手動單次送出也有 TX。
- 暫停保持追蹤，停止顯示延遲處理後結束追蹤；Level 0 不套用，65535 顯示警告且不送非法 Level。
- 自動跟隨關閉後，目標 10 與手動回報 8 可同時存在。
- 心率失聯時觸發異常停止，確認 `24 00` 與 `20 02 C9` 各最多三次，最後為送達未知而非假 ACK。
- TX 篩選、清除確認及另一分頁同步清除；設定版本衝突保留草稿，經「還原目前設定」恢復。
- 390px viewport 的第二分頁可讀取／修改 RPM，數值同步回桌面。DOM 量測沒有文件層級的水平溢出；紀錄表格在自己的容器內橫向捲動。
- 支援 WebMCP 的瀏覽器能列出兩個工具；已測唯讀及數值工具的合法／非法輸入，不支援 WebMCP 的瀏覽器仍使用一般 UI。
- 修正紀錄清除競態後，另以 JavaScript VM 驗證：晚到 DELETE 不得移除更新的事件，新 epoch 同 cursor 仍能清空。

檢視瀏覽器 console 無新增 error／warning；伺服器測試操作未留下未處理例外。README 的兩張圖與 [窄畫面圖片](images/mobile.jpg) 均來自上述實際網頁，沒有使用示意圖或現場資料。

## 尚待現場驗收

1. 使用者確認實際 TCP 主機 IP／port 並手動連線。
2. 與實際平板／APK 測刷卡、課程啟停、阻力設定、狀態查詢及斷線情境。
3. 確認現場對端的 ACK 習慣是否與指定程式及補充證據一致。
4. 從需要使用的區網電腦測網頁可達性；本次未改防火牆。

本工具不驗證或宣稱醫療療效、真實硬體安全、Nm 校正、課程後端完整性或所有 APK 版本互通。交付時停止測試 fixture、將設定還原為原版預填目的地，服務保持運行但 TCP 未連線，交由使用者測試。
