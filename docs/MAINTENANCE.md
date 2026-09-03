# 維護與故障排除

## 設定與備份

預設資料位置是啟動時工作目錄下的 `instance/`。可用 `--instance-dir` 或 `REHAB_INSTANCE_DIR` 指向明確路徑，避免不同工作目錄讀到不同設定。

`settings.json` 只存 `socket_server`、`socket_port`、`auto_reconnect`。請先斷線再備份／更換檔案，外部編輯後重新啟動；不要直接編輯仍運作中的設定檔並期待立即載入。`config.example.json` 是人工預設範例，可在首次啟動前複製成資料目錄內的 `settings.json`。

設定採同目錄暫存檔、flush／fsync 及原子 replace。儲存失敗會回報錯誤並保留原檔；不要將資料目錄設在唯讀位置。損毀設定不會靜默改連其他主機，需修正設定後重新啟動。

不持久化卡號、數值、追蹤、連線或事件。關閉／重新啟動之前若需保存調試資料，按「下載 JSON」。事件可能包含真實卡號與現場 IP；請去識別化後再分享，絕不要直接提交到 Git。

## 啟動與停止

依 README 的前景命令啟動並以 `Ctrl+C` 停止。測試交付時若由背景程序啟動，在 PowerShell 找出 **此專案的** 監聽程序，確認完整命令列再停止：

```powershell
Get-NetTCPConnection -LocalPort 5000 -State Listen | Select-Object LocalAddress, OwningProcess
Get-CimInstance Win32_Process -Filter "ProcessId = <上一步的PID>" | Select-Object ProcessId, CommandLine
Stop-Process -Id <已確認的PID>
```

不要依 `python` 名稱批次停止所有程序，也不要在同一資料目錄／port 同時啟動多份服務。背景強制停止不保證保留記憶體事件；先下載並在網頁斷線。

## 常見故障

| 現象 | 檢查方式 |
| --- | --- |
| 本機網頁開不了 | 確認 Python 命令正常、5000 沒被占用；看終端錯誤；必要時換 `--web-port 5001` |
| 其他電腦開不了網頁 | 使用執行電腦區網 IP，不是 127.0.0.1；確認監聽 0.0.0.0、路由／防火牆政策 |
| TCP 拒絕連線 | 對方需真的執行 TCP server；確認 `socket_server`／port，不是 HTTP 網頁或 Parse URL |
| TCP 連線逾時 | 檢查可達網段、主機在線、防火牆；connect timeout 3 秒，但 DNS 解析可能較久 |
| 一直重試 | 按「斷線」終止使用者連線意圖；等狀態已斷線才修改目的地 |
| 主機關閉後斷線 | EOF 是正常可判定的斷線；開啟自動重連才會再嘗試，不自動重送舊卡號 |
| 刷卡成功但課程沒出現 | 卡號 ACK 只表示對端回覆，不代表課程存在；課程／帳號後端不在本工具範圍 |
| 數值沒有 TX | 檢查已連線、追蹤中、自動回報勾選、值真的改變；相同值可按單次送出 |
| Level 目標與回報不同 | 自動跟隨是否關閉？目標是否 0？是否手動改過？超過 255 是否被擋下？ |
| 暫停後仍送出 | 此為指定程式的相容行為；暫停只 ACK，停止命令才在延遲後終止 tracking |
| 收到資料但不是已確認 | 控制指令可結束 legacy 等待；看 RX 命令，不要假設任何回覆都是 ACK |
| 三次傳送仍未確認 | 對端可能未定義 ACK；先看對端實際處理，這不是確定失敗，不要無限重試刷卡 |
| 設定版本衝突 | 別的分頁已修改；按「還原目前設定」後重新編輯，避免覆蓋別人操作 |
| UI 顯示更新中斷 | 檢查 HTTP 服務；瀏覽器重新整理不會自動建立 TCP 連線 |

## 紀錄判讀

TX 為已寫入 TCP 的 bytes，RX 為通過拆包校驗的資料；不合法資料與狀態變化列為 SYS。時間含毫秒；HEX 為真正線上內容；TX 的次數 1 為首次、2／3 為重送。成功寫入 OS socket 不等於對端應用已處理。

最多 1000 筆，前端篩選不影響後端保留範圍；下載包含所有目前事件，不只畫面篩選結果。「清除」對全部分頁生效。每頁以 cursor／epoch 增量更新；程序重啟用 instance_id 偵測並清掉舊頁面紀錄。

## 測試與版本更新

```bash
python -m pip install -c constraints.txt -e ".[test]"
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m build
```

`tests/test_protocol.py` 檢查固定封包與拆包，`test_simulator.py` 檢查純狀態機，`test_transport.py` 使用 ephemeral loopback TCP 對端，`test_api_storage.py` 使用 Flask test client／暫存設定。測試不依賴 9999、不讀取現場設定、不連外部主機。

更新依賴需同時維護 `pyproject.toml` 與 `constraints.txt`，在全新 Python 3.12 虛擬環境重跑 Windows／Ubuntu CI。constraints 是本次測試依賴版本紀錄，不是安全稽核證明。單程序限制不因換 HTTP server 而消失。

## 擴充命令

1. 先在 `docs/PROTOCOL.md` 記錄可追溯來源，確認方向、資料長度、ACK 及重送，避免猜測。
2. 補固定 bytes、邊界、錯包與 ACK 交錯測試；需要時新增 `protocol.py` 編解碼。
3. 在 `simulator.py` 實作純狀態變化，由 monotonic tick 處理延遲，不能 sleep 阻塞接收。
4. 由 `transport.py` 排隊送出，不在 HTTP handler 或其他執行緒自行 recv。
5. API 做原子輸入驗證，再新增 UI，更新架構／使用文件與實際截圖。

## 更新 README 截圖

只使用人工 UID，切勿為了截圖連到現場主機或傳真實卡號。`tests/browser_fixture.py` 是測試限定的短暫 loopback fixture，不是產品的假主機功能，也不打包進 Python wheel。

1. 在本機前景啟動應用。
2. 另開終端，在專案虛擬環境執行 `python tests/browser_fixture.py --duration 900`。記下 `TEST_PEER_PORT`；只在 127.0.0.1 監聽，到期自動結束，亦可 Ctrl+C 提前停止。
3. 網頁斷線後儲存 `127.0.0.1` 與臨時 port，關閉自動重連。把讀卡器／心率裝置設為正常，輸入 RPM 64、BPM 92。
4. 連線後刷人工 UID `01234567`。fixture 回覆 UID、下開始及 Level 10；確認實際 TX／RX。可改值驗證變值回報。
5. fixture 另以人工 UID `00000000`／`00000001`／`00000002`／`00000003` 觸發暫停／停止／Level 0／Level 65535；僅供 UI 驗證。
6. 以瀏覽器實際截圖更新 `docs/images/dashboard.jpg` 與 `docs/images/protocol-log.jpg`，檢查桌面與 390px 較窄畫面。保存工具輸出的原生 JPEG；若換工具回傳 PNG，需同步修改副檔名與資產測試。不可用示意圖冒充截圖。
7. 結束後斷線、停止 fixture、還原目的地設定，再重新啟動應用，確認為未連線狀態。

正式安裝與日常使用不需要此 fixture；真實主機仍由使用者提供。
