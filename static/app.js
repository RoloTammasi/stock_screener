const table = document.getElementById("resultsTable");
const statusEl = document.getElementById("status");
const filterInput = document.getElementById("filterInput");
const themeToggle = document.getElementById("themeToggle");
const clearCacheButton = document.getElementById("clearCacheButton");
const runButton = document.getElementById("runButton");
const stopRunButton = document.getElementById("stopRunButton");
const runState = document.getElementById("runState");
const runProgressText = document.getElementById("runProgressText");
const runProgress = document.getElementById("runProgress");
const runLog = document.getElementById("runLog");
const tabs = Array.from(document.querySelectorAll(".tab"));
const watchlistStorageKey = "deepValueWatchlist";

let state = {
  kind: "filtered",
  rows: [],
  columns: [],
  sortColumn: "rank",
  sortDirection: "asc",
  filter: "",
};

const numericColumns = new Set([
  "rank",
  "price",
  "shares",
  "market_cap",
  "current_assets",
  "current_liabilities",
  "net_current_assets",
  "current_ratio",
  "market_cap_to_nca",
  "nca_per_share",
  "debt",
  "debt_to_nca",
  "margin_of_safety",
  "pe_ratio",
  "valuation_score",
]);

const columnTooltips = {
  watchlist: "Check this box to add the company to your watchlist tab.",
  ticker: "Stock ticker symbol.",
  company_name: "Company name reported by the data provider.",
  price: "Latest share price from the provider.",
  shares: "Shares outstanding used for per-share calculations.",
  market_cap: "Market capitalization: price multiplied by shares outstanding.",
  current_assets: "Assets expected to become cash or be used within one year.",
  current_liabilities: "Obligations due within one year.",
  net_current_assets: "NCA: current assets minus current liabilities.",
  current_ratio: "Current assets at least 1.5 times current liabilities.",
  market_cap_to_nca: "Percentage of what the current market cap is compared to the NCA valuation.",
  nca_per_share: "Projected stock price using NCA valuation.",
  margin_of_safety: "Two thirds of NCA/Share",
  pe_ratio: "Current price should not be more than 15 times average earnings of the past three years.",
  debt: "Reported short-term plus long-term debt when available.",
  debt_to_nca: "Debt not more than 110% of net current assets (for industrial companies).",
};

const columnLabels = {
  watchlist: "",
  ticker: "Symbol",
  company_name: "Name",
  price: "Price",
  shares: "Shares",
  market_cap: "Market Cap (MC)",
  current_assets: "Current Assets",
  current_liabilities: "Current Liabilities",
  net_current_assets: "Net Current Assets (NCA)",
  current_ratio: "Current Ratio",
  market_cap_to_nca: "MC/NCA",
  nca_per_share: "NCA/Share",
  margin_of_safety: "Margin of Safety",
  pe_ratio: "PE Ratio",
  debt: "Debt",
  debt_to_nca: "Debt/NCA",
};

const columnClasses = {
  watchlist: "col-watchlist",
  ticker: "col-symbol",
  company_name: "col-name",
  price: "col-price",
  shares: "col-shares",
  market_cap: "col-market-cap",
  current_assets: "col-current-assets",
  current_liabilities: "col-current-liabilities",
  net_current_assets: "col-net-current-assets",
  current_ratio: "col-current-ratio",
  market_cap_to_nca: "col-mc-nca",
  nca_per_share: "col-nca-share",
  margin_of_safety: "col-margin-safety",
  pe_ratio: "col-pe-ratio",
  debt: "col-debt",
  debt_to_nca: "col-debt-nca",
};

const displayColumns = [
  "watchlist",
  "ticker",
  "company_name",
  "price",
  "shares",
  "market_cap",
  "current_assets",
  "current_liabilities",
  "net_current_assets",
  "current_ratio",
  "market_cap_to_nca",
  "nca_per_share",
  "margin_of_safety",
  "pe_ratio",
  "debt",
  "debt_to_nca",
];

let watchlist = readWatchlist();
let runPollTimer = null;
let lastRunFinishedAt = null;

function formatHeader(value) {
  if (columnLabels[value] !== undefined) return columnLabels[value];
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .replace("Nca", "NCA");
}

function formatCell(column, value) {
  if (column === "margin_of_safety") return formatCell("nca_per_share", value);
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  if (!numericColumns.has(column)) return String(value);
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  if (["rank", "valuation_score"].includes(column)) return number.toFixed(0);
  if (column === "market_cap_to_nca") return `${(number * 100).toFixed(2)}%`;
  if (["current_ratio", "debt_to_nca"].includes(column)) {
    return number.toFixed(2);
  }
  if (Math.abs(number) >= 1_000_000_000) return `${(number / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(number) >= 1_000_000) return `${(number / 1_000_000).toFixed(2)}M`;
  return number.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function yahooFinanceUrl(ticker) {
  return `https://finance.yahoo.com/quote/${encodeURIComponent(String(ticker).trim())}`;
}

function googleSearchUrl(companyName) {
  return `https://www.google.com/search?q=${encodeURIComponent(`"${String(companyName).trim()}"`)}`;
}

async function loadResults(kind) {
  statusEl.textContent = "Loading results...";
  table.querySelector("thead").innerHTML = "";
  table.querySelector("tbody").innerHTML = "";
  try {
    const apiKind = kind === "watchlist" ? "all" : kind;
    const response = await fetch(`/api/results?kind=${apiKind}`);
    if (!response.ok) throw new Error(`Request failed with ${response.status}`);
    const payload = await response.json();
    state.rows = (payload.rows || []).map(enrichRow);
    state.columns = payload.columns || [];
    statusEl.textContent = payload.message || "";
    render();
  } catch (error) {
    statusEl.textContent = `Could not load results: ${error.message}`;
  }
}

function render() {
  renderHeader();
  renderBody();
}

function renderHeader() {
  const head = table.querySelector("thead");
  head.innerHTML = "";
  if (!visibleColumns().length) return;
  const tr = document.createElement("tr");
  for (const column of visibleColumns()) {
    const th = document.createElement("th");
    th.classList.add(columnClasses[column] || "col-default");
    const label = document.createElement("span");
    label.className = "column-label";
    label.textContent = formatHeader(column).trim();
    const sort = document.createElement("span");
    sort.className = "sort-glyph";
    sort.textContent = sortGlyph(column);
    th.append(label, sort);
    th.tabIndex = 0;
    th.dataset.tooltip = columnTooltips[column] || "Screening field.";
    th.setAttribute("aria-label", `${formatHeader(column).trim()}. ${th.dataset.tooltip}`);
    th.addEventListener("click", () => setSort(column));
    tr.appendChild(th);
  }
  head.appendChild(tr);
}

function renderBody() {
  const body = table.querySelector("tbody");
  body.innerHTML = "";
  const rows = filteredRows().sort(compareRows);
  statusEl.textContent = rows.length ? `${rows.length.toLocaleString()} rows` : "No matching rows";
  const fragment = document.createDocumentFragment();
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const column of visibleColumns()) {
      const td = document.createElement("td");
      td.classList.add(columnClasses[column] || "col-default");
      if (column === "watchlist") {
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "watchlist-checkbox";
        checkbox.checked = watchlist.has(row.ticker);
        checkbox.setAttribute("aria-label", `Add ${row.ticker} to watchlist`);
        checkbox.addEventListener("change", () => toggleWatchlist(row.ticker, checkbox.checked));
        td.classList.add("watchlist-cell");
        td.appendChild(checkbox);
      } else if (column === "ticker" && row[column]) {
        const link = document.createElement("a");
        link.href = yahooFinanceUrl(row[column]);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = formatCell(column, row[column]);
        link.title = `Open ${row[column]} on Yahoo Finance`;
        td.appendChild(link);
      } else if (column === "company_name" && row[column]) {
        const link = document.createElement("a");
        link.href = googleSearchUrl(row[column]);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = formatCell(column, row[column]);
        link.title = `Search Google for ${row[column]}`;
        td.appendChild(link);
      } else {
        td.textContent = formatCell(column, row[column]);
      }
      if (numericColumns.has(column)) td.classList.add("numeric");
      tr.appendChild(td);
    }
    fragment.appendChild(tr);
  }
  body.appendChild(fragment);
}

function filteredRows() {
  const baseRows = state.kind === "watchlist"
    ? state.rows.filter((row) => watchlist.has(row.ticker))
    : state.rows;
  const needle = state.filter.trim().toLowerCase();
  if (!needle) return [...baseRows];
  return baseRows.filter((row) =>
    visibleColumns().some((column) => String(row[column] ?? "").toLowerCase().includes(needle)),
  );
}

function compareRows(left, right) {
  const column = state.sortColumn;
  const direction = state.sortDirection === "asc" ? 1 : -1;
  const leftValue = left[column];
  const rightValue = right[column];
  if (numericColumns.has(column)) {
    return (Number(leftValue ?? Infinity) - Number(rightValue ?? Infinity)) * direction;
  }
  return String(leftValue ?? "").localeCompare(String(rightValue ?? "")) * direction;
}

function setSort(column) {
  if (state.sortColumn === column) {
    state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
  } else {
    state.sortColumn = column;
    state.sortDirection = "asc";
  }
  render();
}

function sortGlyph(column) {
  if (state.sortColumn !== column) return "";
  return state.sortDirection === "asc" ? "^" : "v";
}

function visibleColumns() {
  return displayColumns;
}

function enrichRow(row) {
  const ncaPerShare = Number(row.nca_per_share);
  return {
    ...row,
    margin_of_safety: Number.isFinite(ncaPerShare) ? ncaPerShare * (2 / 3) : null,
    pe_ratio: row.pe_ratio ?? null,
  };
}

function readWatchlist() {
  try {
    return new Set(JSON.parse(localStorage.getItem(watchlistStorageKey) || "[]"));
  } catch {
    return new Set();
  }
}

function saveWatchlist() {
  localStorage.setItem(watchlistStorageKey, JSON.stringify([...watchlist].sort()));
}

function toggleWatchlist(ticker, checked) {
  if (!ticker) return;
  if (checked) {
    watchlist.add(ticker);
  } else {
    watchlist.delete(ticker);
  }
  saveWatchlist();
  if (state.kind === "watchlist") render();
}

async function startScreenerRun() {
  setRunButtons(true);
  appendLocalRunLog("Requesting screener run...");
  try {
    const response = await fetch("/api/run", { method: "POST" });
    const payload = await response.json();
    renderRunStatus(payload);
    if (!response.ok && response.status !== 409) {
      throw new Error(payload.message || `Request failed with ${response.status}`);
    }
    pollRunStatus(true);
  } catch (error) {
    appendLocalRunLog(`Could not start run: ${error.message}`);
    setRunButtons(false);
  }
}

async function stopScreenerRun() {
  stopRunButton.disabled = true;
  appendLocalRunLog("Stop requested...");
  try {
    const response = await fetch("/api/stop-run", { method: "POST" });
    const payload = await response.json();
    renderRunStatus(payload);
    pollRunStatus(true);
  } catch (error) {
    appendLocalRunLog(`Could not stop run: ${error.message}`);
  }
}

async function clearCache() {
  clearCacheButton.disabled = true;
  appendLocalRunLog("Emptying API cache...");
  try {
    const response = await fetch("/api/cache/clear", { method: "POST" });
    const payload = await response.json();
    appendLocalRunLog(payload.message || "Cache emptied.");
    if (!response.ok) {
      throw new Error(payload.message || `Request failed with ${response.status}`);
    }
  } catch (error) {
    appendLocalRunLog(`Could not empty cache: ${error.message}`);
  } finally {
    clearCacheButton.disabled = false;
  }
}

async function pollRunStatus(keepPolling = false) {
  window.clearTimeout(runPollTimer);
  try {
    const response = await fetch("/api/run-status");
    if (!response.ok) throw new Error(`Request failed with ${response.status}`);
    const payload = await response.json();
    renderRunStatus(payload);
    if (payload.running || keepPolling) {
      if (payload.running) {
        runPollTimer = window.setTimeout(() => pollRunStatus(true), 1500);
      }
    }
    if (
      payload.status === "completed" &&
      payload.finished_at &&
      payload.finished_at !== lastRunFinishedAt
    ) {
      lastRunFinishedAt = payload.finished_at;
      loadResults(state.kind);
    }
  } catch (error) {
    appendLocalRunLog(`Could not read run status: ${error.message}`);
    setRunButtons(false);
  }
}

function renderRunStatus(payload) {
  const status = payload.status || "idle";
  const running = Boolean(payload.running);
  const processed = Number(payload.processed || 0);
  const total = Number(payload.total || 0);
  const rows = Number(payload.rows || 0);
  const errors = Number(payload.errors || 0);
  const percent = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;

  runState.textContent = statusLabel(status, running);
  runProgress.value = percent;
  runProgressText.textContent = total > 0
    ? `${processed.toLocaleString()} of ${total.toLocaleString()} tickers (${percent}%). Rows kept: ${rows.toLocaleString()}. Errors: ${errors.toLocaleString()}.`
    : payload.message || "No run active.";

  const logs = Array.isArray(payload.logs) && payload.logs.length
    ? payload.logs.join("\n")
    : "No run logs yet.";
  runLog.textContent = logs;
  runLog.scrollTop = runLog.scrollHeight;
  setRunButtons(running, Boolean(payload.cancel_requested));
}

function statusLabel(status, running) {
  if (running) return "Running";
  if (status === "completed") return "Completed";
  if (status === "cancelled") return "Stopped";
  if (status === "failed") return "Failed";
  return "Idle";
}

function setRunButtons(running, cancelRequested = false) {
  runButton.disabled = running;
  stopRunButton.disabled = !running || cancelRequested;
  clearCacheButton.disabled = running;
}

function appendLocalRunLog(message) {
  const current = runLog.textContent === "No run logs yet." ? "" : runLog.textContent;
  const timestamp = formatLogTimestamp(new Date());
  runLog.textContent = `${current}${current ? "\n" : ""}[${timestamp}] ${message}`;
  runLog.scrollTop = runLog.scrollHeight;
}

function formatLogTimestamp(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join("-") + ` ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

filterInput.addEventListener("input", (event) => {
  state.filter = event.target.value;
  renderBody();
});

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    state.kind = tab.dataset.kind;
    loadResults(state.kind);
  });
});

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const dark = theme === "dark";
  themeToggle.textContent = dark ? "Light mode" : "Dark mode";
  themeToggle.setAttribute("aria-pressed", String(dark));
}

themeToggle.addEventListener("click", () => {
  const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem("deepValueTheme", nextTheme);
  applyTheme(nextTheme);
});

runButton.addEventListener("click", startScreenerRun);
stopRunButton.addEventListener("click", stopScreenerRun);
clearCacheButton.addEventListener("click", clearCache);

applyTheme(localStorage.getItem("deepValueTheme") || "light");
loadResults(state.kind);
pollRunStatus(false);
