"use strict";
const $ = (id) => document.getElementById(id);
const fields = ["rpm", "heart_rate", "reported_level"];
let state = null, events = [], cursor = 0, epoch = null, settingsDirty = false, settingsBaseRevision = 0;
let pollBusy = false, toastTimer, eventVersion = 0, renderedVersion = -1;
const editing = new Set();
const pendingEdits = new Map(), retiredInstances = new Set();
let simulationChain = Promise.resolve(), savingSettings = false;
const fieldBusy = (id) => editing.has(id) || (pendingEdits.get(id) || 0) > 0;
const connectionNames = {connected:"已連線", disconnected:"未連線", connecting:"連線中", reconnecting:"等待重連", disconnecting:"斷線中", error:"連線失敗"};
const deliveryNames = {waiting:"等待主機回覆", sent:"已寫入 TCP", confirmed:"主機已確認卡號", rejected:"主機拒絕卡號", received:"收到資料，未確認 ACK", timeout:"等待逾時・送達未知", cancelled:"已取消待送事件"};

async function api(path, method = "GET", data) {
  const options = {method, cache:"no-store"};
  if (data !== undefined) { options.headers = {"Content-Type":"application/json"}; options.body = JSON.stringify(data); }
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}
function toast(message, error = false) {
  clearTimeout(toastTimer); $("toast").textContent = message; $("toast").className = `toast${error ? " error" : ""}`; $("toast").hidden = false;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, error ? 6500 : 3500);
}
async function act(fn, success) {
  try { const result = await fn(); if (result?.simulation) render(result); if (success) toast(success); return result; }
  catch (error) { toast(error.message, true); return null; }
}
function setValue(id, value) { if (document.activeElement !== $(id) && !fieldBusy(id)) $(id).value = value; }
function restoreSettings() {
  if (!state) return;
  settingsDirty = false; settingsBaseRevision = state.settings_revision;
  $("socketServer").value = state.settings.socket_server; $("socketPort").value = state.settings.socket_port; $("autoReconnect").checked = state.settings.auto_reconnect;
  render(state);
}
function markSettingsDirty() {
  if (!settingsDirty) settingsBaseRevision = state.settings_revision;
  settingsDirty = true; render(state);
}
function render(next) {
  if (state && state.instance_id === next.instance_id && next.snapshot_sequence < state.snapshot_sequence) return;
  if (state && state.instance_id !== next.instance_id) {
    if (retiredInstances.has(next.instance_id)) return;
    retiredInstances.add(state.instance_id);
    events = []; cursor = 0; epoch = null; eventVersion++;
  }
  state = next;
  const connection = next.connection, sim = next.simulation;
  const connected = connection.status === "connected" && connection.desired;
  const locked = connection.desired || !["disconnected", "error"].includes(connection.status);
  $("connectionBadge").className = `status-badge ${connection.status}`;
  $("connectionBadge").querySelector("span").textContent = connectionNames[connection.status] || connection.status;
  $("connectionHint").textContent = connected ? "TCP 連線正常" : locked ? "正在處理連線，請稍候" : "啟動時不會自動連線";
  for (const id of ["socketServer", "socketPort", "autoReconnect", "saveSettings"]) $(id).disabled = locked || savingSettings;
  $("connectButton").disabled = locked || settingsDirty || savingSettings;
  $("resetSettings").disabled = savingSettings;
  $("disconnectButton").disabled = !locked || connection.status === "disconnecting";
  $("resetSettings").hidden = !settingsDirty;
  if (!settingsDirty) {
    setValue("socketServer", next.settings.socket_server); setValue("socketPort", next.settings.socket_port);
    $("autoReconnect").checked = next.settings.auto_reconnect; settingsBaseRevision = next.settings_revision;
  }
  $("targetText").textContent = `${connected ? "目前連線" : "已儲存目標"}  ${next.settings.socket_server}:${next.settings.socket_port}`;
  $("settingsNote").textContent = settingsDirty ? (settingsBaseRevision !== next.settings_revision ? "其他分頁已修改設定；請還原目前設定後重新編輯。" : "有未儲存變更，請先儲存再連線。") : "網頁修改的是傳送目的地，不是網頁監聽位址。";
  for (const name of fields) {
    $(name + "Display").textContent = sim[name];
    if (!fieldBusy(name + "Input") && !fieldBusy(name + "Range")) {
      setValue(name + "Input", sim[name]); setValue(name + "Range", Math.min(255, sim[name]));
    }
    $(name + "Sent").textContent = sim.last_sent[name] ?? "—";
  }
  for (const [id, key] of [["autoReport","auto_report"],["autoFollow","auto_follow"],["readerReady","reader_ready"],["hrReady","hr_ready"]]) {
    if (!fieldBusy(id)) $(id).checked = sim[key];
  }
  setValue("machineState", sim.machine_state);
  $("targetLevel").textContent = sim.target_level ?? "—";
  $("lastCommand").textContent = sim.last_command ?? "等待主機指令";
  $("trackingBadge").textContent = sim.tracking ? "追蹤中" : "未追蹤";
  $("trackingBadge").className = `state-tag${sim.tracking ? " active" : ""}`;
  $("trackingHint").textContent = !connected ? "尚未連線；數值可先調整，連線後才能送出。" : sim.tracking && sim.auto_report ? "追蹤中・數值變更時自動回報；單次送出可強制重送。" : "尚未啟用變值回報；單次送出可略過追蹤條件。";
  $("pendingControl").hidden = !sim.pending_control;
  if (sim.pending_control) $("pendingControl").textContent = `${sim.pending_control.action}指令處理中，保留原版延遲…其他指令仍可接收。`;
  $("levelWarning").hidden = !sim.level_warning; $("levelWarning").textContent = sim.level_warning ?? "";
  $("sendCard").disabled = !connected; $("sendFault").disabled = !connected;
  document.querySelectorAll(".send-telemetry").forEach(button => { button.disabled = !connected; });
  $("txCount").textContent = connection.tx_count; $("rxCount").textContent = connection.rx_count;
  const delivery = next.last_delivery;
  $("deliveryTitle").textContent = delivery ? deliveryNames[delivery.status] || delivery.status : "尚無事件";
  $("deliveryDetail").textContent = delivery ? delivery.detail : "TCP 寫入成功不等於主機已確認。";
  $("serviceError").hidden = !connection.error;
  $("serviceError").textContent = connection.error || "";
}
function renderEvents(force = false) {
  if (!force && eventVersion === renderedVersion) return;
  renderedVersion = eventVersion;
  const filter = $("eventFilter").value;
  const rows = events.filter(event => filter === "all" || event.direction === filter).slice().reverse();
  $("eventCount").textContent = events.length;
  const fragment = document.createDocumentFragment();
  for (const event of rows) {
    const row = document.createElement("tr"); if (event.level === "warning") row.className = "event-warning";
    const timeCell = document.createElement("td"); timeCell.textContent = new Date(event.time).toLocaleTimeString("zh-TW", {hour12:false}) + "." + new Date(event.time).getMilliseconds().toString().padStart(3,"0");
    const directionCell = document.createElement("td"); const badge = document.createElement("span"); badge.className = `direction ${event.direction}`; badge.textContent = event.direction; directionCell.append(badge);
    const commandCell = document.createElement("td"); commandCell.textContent = event.command ?? "—";
    const detailCell = document.createElement("td"); const detail = document.createElement("span"); detail.className = "event-detail"; detail.textContent = event.detail; detailCell.append(detail);
    if (event.hex) { const hex = document.createElement("code"); hex.className = "hex-line"; hex.textContent = event.hex; detailCell.append(hex); }
    const checkCell = document.createElement("td"); checkCell.textContent = (event.checksum === "valid" ? "✓ 有效" : event.checksum === "invalid" ? "錯誤" : "—") + (event.attempt ? ` / ${event.attempt}` : "");
    row.append(timeCell,directionCell,commandCell,detailCell,checkCell); fragment.append(row);
  }
  if (!rows.length) { const row = document.createElement("tr"); const cell = document.createElement("td"); cell.colSpan = 5; cell.className = "empty-state"; cell.textContent = "此篩選下尚無紀錄。連線後可檢查每一筆收送。"; row.append(cell); fragment.append(row); }
  $("eventsBody").replaceChildren(fragment);
}
function acceptEvents(result) {
  if (state && result.instance_id !== state.instance_id) return;
  if (epoch !== null && (result.epoch < epoch || (result.epoch === epoch && result.cursor < cursor))) return;
  if (result.reset || (epoch !== null && epoch !== result.epoch)) { events = []; eventVersion++; }
  const seen = new Set(events.map(event => event.id));
  for (const event of result.events) if (!seen.has(event.id)) { events.push(event); eventVersion++; }
  events = events.slice(-1000); cursor = result.cursor; epoch = result.epoch; renderEvents();
}
async function poll() {
  if (pollBusy) return;
  pollBusy = true;
  try {
    const [next, result] = await Promise.all([api("/api/state"), api(`/api/events?after=${cursor}${epoch === null ? "" : `&epoch=${epoch}`}`)]);
    render(next); acceptEvents(result); $("pollStatus").textContent = "即時同步";
  } catch (error) {
    $("pollStatus").textContent = "同步中斷"; $("serviceError").hidden = false; $("serviceError").textContent = "無法取得服務狀態，請確認程式仍在執行。";
    for (const id of ["connectButton","sendCard","sendFault"]) $(id).disabled = true;
    document.querySelectorAll(".send-telemetry").forEach(button => { button.disabled = true; });
  } finally { pollBusy = false; }
}
function confirmAction(title, description) {
  $("confirmTitle").textContent = title; $("confirmDescription").textContent = description;
  const dialog = $("confirmDialog"); dialog.returnValue = "cancel"; dialog.showModal();
  return new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once:true}));
}
async function updateField(id, changes) {
  editing.delete(id);
  pendingEdits.set(id, (pendingEdits.get(id) || 0) + 1);
  const operation = simulationChain.then(async () => {
    const result = await act(() => api("/api/simulation", "PUT", changes));
    pendingEdits.set(id, pendingEdits.get(id) - 1);
    if (result) render(result); else await poll();
  });
  simulationChain = operation.catch(() => {});
  return operation;
}
$("settingsForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (!$("settingsForm").reportValidity()) return;
  savingSettings = true; render(state);
  const result = await act(() => api("/api/settings", "PUT", {socket_server:$("socketServer").value.trim(), socket_port:Number($("socketPort").value), auto_reconnect:$("autoReconnect").checked, revision:settingsBaseRevision}));
  savingSettings = false;
  if (result) { render(result); restoreSettings(); toast("設定已儲存，請按「連線主機」建立連線。"); }
  else render(state);
});
for (const id of ["socketServer","socketPort","autoReconnect"]) $(id).addEventListener("input", () => { if (state) markSettingsDirty(); });
$("resetSettings").addEventListener("click", restoreSettings);
$("connectButton").addEventListener("click", () => act(() => api("/api/connection", "POST", {action:"connect"})));
$("disconnectButton").addEventListener("click", () => act(() => api("/api/connection", "POST", {action:"disconnect"})));
for (const name of fields) {
  const range = $(name+"Range"), input = $(name+"Input");
  for (const element of [range,input]) {
    element.addEventListener("input", () => { editing.add(element.id); if (element === range) input.value = range.value; else if (input.validity.valid) range.value = input.value; });
    const commitInput = () => {
      if (!editing.has(element.id)) return;
      if (!element.reportValidity() || element.value === "") { toast("數值必須是 0–255 的整數。",true); editing.delete(element.id); if (state) render(state); return; }
      updateField(element.id, {[name]:Number(element.value)});
    };
    element.addEventListener("change", commitInput);
    element.addEventListener("blur", () => { commitInput(); editing.delete(element.id); });
  }
}
for (const [id,key] of [["autoReport","auto_report"],["autoFollow","auto_follow"],["readerReady","reader_ready"],["hrReady","hr_ready"]]) $(id).addEventListener("change", () => updateField(id, {[key]:$(id).checked}));
$("machineState").addEventListener("change", () => updateField("machineState", {machine_state:$("machineState").value}));
document.querySelectorAll(".send-telemetry").forEach(button => button.addEventListener("click", () => {
  const name = button.dataset.field, input = $(name+"Input");
  if (!input.reportValidity() || input.value === "") { toast("數值必須是 0–255 的整數。",true); return; }
  act(() => api("/api/send", "POST", {type:"telemetry", field:name, value:Number(input.value)}), "單次傳送已排入佇列。");
}));
$("sendCard").addEventListener("click", () => act(() => api("/api/send", "POST", {type:"rfid",uid:$("cardUid").value}), "刷卡已排入佇列，請查看主機回覆。"));
$("sendFault").addEventListener("click", async () => {
  if (await confirmAction("觸發異常停止？", "此操作會停止模擬追蹤並傳送 0x24。若心率裝置為失聯，另送出 0x20 狀態通知。")) await act(() => api("/api/send", "POST", {type:"unexpected_stop"}));
});
$("eventFilter").addEventListener("change", () => renderEvents(true));
$("clearEvents").addEventListener("click", async () => {
  if (await confirmAction("清除通訊紀錄？", "所有分頁共用的記憶體紀錄將清除，無法還原。需要保留時請先下載 JSON。")) {
    const result = await act(() => api("/api/events", "DELETE", {})); if (result) acceptEvents(result);
  }
});

// Optional imperative WebMCP surface; uses exactly the same validated HTTP boundaries.
if (document.modelContext?.registerTool) {
  const lifecycle = new AbortController();
  const tool = {name:"read_simulator_state", title:"讀取機台模擬狀態", description:"唯讀取得連線、機台與數值狀態，不會建立 TCP 連線。", inputSchema:{type:"object",properties:{},additionalProperties:false}, annotations:{readOnlyHint:true}, async execute(input) {
    if (!input || Array.isArray(input) || typeof input !== "object" || Object.keys(input).length) throw new Error("請提供空物件");
    const result = await api("/api/state"); render(result); return result;
  }};
  const configure = {name:"configure_simulation_values",title:"設定模擬數值",description:"設定 RPM、BPM 或回報 Level；已連線且追蹤中時可能立即自動回報至目前主機。不建立或更改 TCP 連線。",inputSchema:{type:"object",properties:{rpm:{type:"integer",minimum:0,maximum:255},heart_rate:{type:"integer",minimum:0,maximum:255},reported_level:{type:"integer",minimum:0,maximum:255}},minProperties:1,additionalProperties:false},annotations:{readOnlyHint:false},async execute(input) {
    if (!input || Array.isArray(input) || typeof input !== "object" || !Object.keys(input).length || Object.entries(input).some(([key,value]) => !fields.includes(key) || !Number.isInteger(value) || value < 0 || value > 255)) throw new Error("請提供至少一個合法的 0–255 整數數值");
    await simulationChain;
    const result = await api("/api/simulation","PUT",input); render(result); return result.simulation;
  }};
  for (const entry of [tool,configure]) {
    try { Promise.resolve(document.modelContext.registerTool(entry,{signal:lifecycle.signal})).catch(() => {}); } catch (_) { /* Unsupported experimental interface is optional. */ }
  }
  window.addEventListener("pagehide", () => lifecycle.abort(), {once:true});
}
poll(); setInterval(poll, 500);
