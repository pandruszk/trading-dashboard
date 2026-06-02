/* ============================================================
   Trading Dashboard — Client-Side Logic
   ============================================================ */

// State
let refreshInterval = null;
let marketOpen = false;
let charts = {};
let sortState = { holdings: { col: "market_value", dir: "desc" }, trades: { col: null, dir: null } };
let tradeFilter = "ALL";

// ============================================================
// INIT
// ============================================================
document.addEventListener("DOMContentLoaded", () => {
    initKell();
    loadAll();
    startAutoRefresh();
});

function loadAll() {
    fetchAccount();
    fetchPositions();
    fetchTrades();
    fetchRisk();
    fetchHistory();
    fetchStatus();
    fetchKell();
    fetchKellScan();
}

function startAutoRefresh() {
    if (refreshInterval) clearInterval(refreshInterval);
    const ms = marketOpen ? 30000 : 300000;
    refreshInterval = setInterval(loadAll, ms);
    updateRefreshDisplay();
}

function updateRefreshDisplay() {
    const el = document.getElementById("refresh-info");
    if (el) {
        const interval = marketOpen ? "30s" : "5m";
        el.textContent = `Auto-refresh: ${interval}`;
    }
}

// ============================================================
// FORMATTING HELPERS
// ============================================================
function fmt$(val, decimals = 2) {
    if (val == null || isNaN(val)) return "—";
    const sign = val >= 0 ? "" : "-";
    return sign + "$" + Math.abs(val).toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

function fmtPct(val) {
    if (val == null || isNaN(val)) return "—";
    const sign = val >= 0 ? "+" : "";
    return sign + (val * 100).toFixed(2) + "%";
}

function fmtPL$(val) {
    if (val == null || isNaN(val)) return "—";
    const sign = val >= 0 ? "+$" : "-$";
    return sign + Math.abs(val).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}

function plClass(val) {
    if (val > 0) return "positive";
    if (val < 0) return "negative";
    return "neutral";
}

function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
}

// ============================================================
// ACCOUNT (Header)
// ============================================================
async function fetchAccount() {
    try {
        const res = await fetch("/api/account");
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        // Market status
        marketOpen = data.market_open;
        const badge = document.getElementById("market-badge");
        if (badge) {
            badge.textContent = data.market_open ? "Market Open" : "Market Closed";
            badge.className = "market-badge " + (data.market_open ? "open" : "closed");
        }

        // Portfolio value
        setContent("equity-value", fmt$(data.equity));

        // Total P&L
        const totalEl = document.getElementById("total-pl");
        if (totalEl) {
            totalEl.textContent = fmtPL$(data.total_pl);
            totalEl.className = "stat-value " + plClass(data.total_pl);
        }
        setContent("total-pl-pct", fmtPct(data.total_pl_pct));

        // Today P&L
        const todayEl = document.getElementById("today-pl");
        if (todayEl) {
            todayEl.textContent = fmtPL$(data.today_pl);
            todayEl.className = "stat-value " + plClass(data.today_pl);
        }
        setContent("today-pl-pct", fmtPct(data.today_pl_pct));

        // Cash
        setContent("cash-value", fmt$(data.cash));

        // Timestamp
        setContent("last-updated", fmtTime(data.timestamp));

        // Update refresh interval if market status changed
        startAutoRefresh();

    } catch (e) {
        console.error("Account fetch error:", e);
    }
}

function setContent(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

// ============================================================
// POSITIONS (Holdings Table)
// ============================================================
let positionsData = [];

async function fetchPositions() {
    try {
        const res = await fetch("/api/positions");
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        positionsData = data.positions || [];
        renderHoldings();
        renderPLChart();
    } catch (e) {
        console.error("Positions fetch error:", e);
        document.getElementById("holdings-body").innerHTML =
            '<tr><td colspan="10" class="error-msg">Failed to load positions</td></tr>';
    }
}

function renderHoldings() {
    const { col, dir } = sortState.holdings;
    const sorted = [...positionsData].sort((a, b) => {
        let va = a[col], vb = b[col];
        if (typeof va === "string") va = va.toLowerCase();
        if (typeof vb === "string") vb = vb.toLowerCase();
        if (va < vb) return dir === "asc" ? -1 : 1;
        if (va > vb) return dir === "asc" ? 1 : -1;
        return 0;
    });

    const tbody = document.getElementById("holdings-body");
    if (!tbody) return;

    tbody.innerHTML = sorted.map(p => `
        <tr>
            <td class="ticker">${p.ticker}</td>
            <td>${p.sector || "—"}</td>
            <td class="right">${p.shares}</td>
            <td class="right">${fmt$(p.entry_price)}</td>
            <td class="right">${fmt$(p.current_price)}</td>
            <td class="right">${fmt$(p.market_value)}</td>
            <td class="right">${(p.weight * 100).toFixed(1)}%</td>
            <td class="right ${plClass(p.pnl)}">${fmtPL$(p.pnl)}</td>
            <td class="right ${plClass(p.pnl_pct)}">${fmtPct(p.pnl_pct)}</td>
            <td class="right">${fmt$(p.effective_stop)} <span style="color:var(--text-muted)">(${(p.distance_to_stop * 100).toFixed(1)}%)</span></td>
        </tr>
    `).join("");

    // Update sort indicators
    document.querySelectorAll("#holdings-table thead th").forEach(th => {
        th.classList.remove("sort-asc", "sort-desc");
        if (th.dataset.col === col) {
            th.classList.add(dir === "asc" ? "sort-asc" : "sort-desc");
        }
    });

    setContent("holdings-count", `${sorted.length} positions`);
}

function sortHoldings(col) {
    const s = sortState.holdings;
    if (s.col === col) {
        s.dir = s.dir === "asc" ? "desc" : "asc";
    } else {
        s.col = col;
        s.dir = "desc";
    }
    renderHoldings();
}

// ============================================================
// CHARTS
// ============================================================

// --- Portfolio Value Over Time ---
async function fetchHistory() {
    try {
        const res = await fetch("/api/history");
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        const history = data.history || [];
        if (history.length === 0) return;

        const labels = history.map(h => h.date);
        const values = history.map(h => h.equity);

        const ctx = document.getElementById("chart-portfolio");
        if (!ctx) return;

        if (charts.portfolio) charts.portfolio.destroy();
        charts.portfolio = new Chart(ctx, {
            type: "line",
            data: {
                labels,
                datasets: [{
                    label: "Portfolio Value",
                    data: values,
                    borderColor: "#58a6ff",
                    backgroundColor: "rgba(88, 166, 255, 0.1)",
                    fill: true,
                    tension: 0.3,
                    pointRadius: 3,
                    pointBackgroundColor: "#58a6ff",
                    borderWidth: 2,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: ctx => "$" + ctx.parsed.y.toLocaleString("en-US", { minimumFractionDigits: 2 }),
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: "rgba(48, 54, 61, 0.5)" },
                        ticks: { color: "#6e7681", font: { size: 10 } },
                    },
                    y: {
                        grid: { color: "rgba(48, 54, 61, 0.5)" },
                        ticks: {
                            color: "#6e7681",
                            font: { size: 10 },
                            callback: v => "$" + v.toLocaleString(),
                        },
                    }
                },
            }
        });
    } catch (e) {
        console.error("History fetch error:", e);
    }
}

// --- Sector Allocation Donut ---
function renderSectorChart(sectors) {
    if (!sectors || sectors.length === 0) return;

    const ctx = document.getElementById("chart-sectors");
    if (!ctx) return;

    const colors = ["#58a6ff", "#3fb950", "#bc8cff", "#f0883e", "#d29922", "#f85149", "#39d2c0", "#8b949e"];

    if (charts.sectors) charts.sectors.destroy();
    charts.sectors = new Chart(ctx, {
        type: "doughnut",
        data: {
            labels: sectors.map(s => s.sector),
            datasets: [{
                data: sectors.map(s => s.value),
                backgroundColor: colors.slice(0, sectors.length),
                borderColor: "#161b22",
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: "60%",
            plugins: {
                legend: {
                    position: "right",
                    labels: {
                        color: "#8b949e",
                        font: { size: 11, family: "'SF Mono', monospace" },
                        padding: 8,
                        usePointStyle: true,
                        pointStyleWidth: 10,
                    }
                },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const val = ctx.parsed;
                            const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
                            const pct = ((val / total) * 100).toFixed(1);
                            return ` $${val.toLocaleString("en-US", { minimumFractionDigits: 0 })} (${pct}%)`;
                        }
                    }
                }
            }
        }
    });
}

// --- P&L Bar Chart ---
function renderPLChart() {
    if (!positionsData || positionsData.length === 0) return;

    const ctx = document.getElementById("chart-pnl");
    if (!ctx) return;

    const sorted = [...positionsData].sort((a, b) => b.pnl - a.pnl);
    const labels = sorted.map(p => p.ticker);
    const values = sorted.map(p => p.pnl);
    const colors = values.map(v => v >= 0 ? "#3fb950" : "#f85149");

    if (charts.pnl) charts.pnl.destroy();
    charts.pnl = new Chart(ctx, {
        type: "bar",
        data: {
            labels,
            datasets: [{
                label: "P&L",
                data: values,
                backgroundColor: colors,
                borderRadius: 3,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: "y",
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const v = ctx.parsed.x;
                            const sign = v >= 0 ? "+$" : "-$";
                            return sign + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2 });
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: "rgba(48, 54, 61, 0.5)" },
                    ticks: {
                        color: "#6e7681",
                        font: { size: 10 },
                        callback: v => (v >= 0 ? "+$" : "-$") + Math.abs(v).toLocaleString(),
                    },
                },
                y: {
                    grid: { display: false },
                    ticks: { color: "#58a6ff", font: { size: 11, weight: "bold" } },
                }
            }
        }
    });
}

// ============================================================
// RISK DASHBOARD
// ============================================================
async function fetchRisk() {
    try {
        const res = await fetch("/api/risk");
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        renderStopBars(data.stops || []);
        renderSectorBars(data.sectors || []);
        renderAlerts(data.alerts || []);
        renderSectorChart(data.sectors || []);
    } catch (e) {
        console.error("Risk fetch error:", e);
    }
}

function renderStopBars(stops) {
    const container = document.getElementById("stop-bars");
    if (!container) return;

    container.innerHTML = stops.map(s => {
        // Distance to stop as percentage (0% = at stop, 100% = far from stop)
        const pct = Math.min(s.distance_to_stop * 100, 100);
        const cls = s.status;
        const serverIcon = s.has_server_stop ? "&#10003;" : "&#10007;";
        const serverCls = s.has_server_stop ? "positive" : "negative";

        return `
            <div class="stop-bar">
                <span class="stop-bar-label">${s.ticker}</span>
                <div class="stop-bar-track">
                    <div class="stop-bar-fill ${cls}" style="width:${pct}%"></div>
                </div>
                <span class="stop-bar-value">${pct.toFixed(1)}%</span>
                <span class="${serverCls}" title="Server stop: ${s.has_server_stop ? '$' + s.server_stop_price?.toFixed(2) : 'MISSING'}">${serverIcon}</span>
            </div>
        `;
    }).join("");
}

function renderSectorBars(sectors) {
    const container = document.getElementById("sector-bars");
    if (!container) return;

    const maxWeight = 0.45;
    container.innerHTML = sectors.map(s => {
        const widthPct = Math.min(s.weight / 0.6 * 100, 100);
        const limitPct = maxWeight / 0.6 * 100;
        const overCls = s.over_weight ? " over" : "";

        return `
            <div class="sector-bar">
                <div class="sector-bar-header">
                    <span class="sector-bar-name">${s.sector} (${s.count})</span>
                    <span class="sector-bar-weight">${(s.weight * 100).toFixed(1)}% — ${s.tickers.join(", ")}</span>
                </div>
                <div class="sector-bar-track">
                    <div class="sector-bar-fill${overCls}" style="width:${widthPct}%"></div>
                    <div class="sector-bar-limit" style="left:${limitPct}%" title="45% limit"></div>
                </div>
            </div>
        `;
    }).join("");
}

function renderAlerts(alerts) {
    const container = document.getElementById("alerts-panel");
    if (!container) return;

    if (alerts.length === 0) {
        container.innerHTML = '<div class="no-alerts">All clear — no alerts</div>';
        return;
    }

    container.innerHTML = alerts.map(a => {
        let cls = "yellow";
        if (a.includes("TRIGGERED") || a.includes("MISSING")) cls = "red";
        if (a.includes("All clear")) cls = "green";
        return `<div class="alert-item ${cls}">${a}</div>`;
    }).join("");

    setContent("alert-count", alerts.length.toString());
}

// ============================================================
// TRADE HISTORY
// ============================================================
let tradesData = [];

async function fetchTrades() {
    try {
        const res = await fetch("/api/trades");
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        tradesData = data.trades || [];
        renderTrades();
    } catch (e) {
        console.error("Trades fetch error:", e);
    }
}

function filterTrades(action) {
    tradeFilter = action;
    document.querySelectorAll(".filter-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.action === action);
    });
    renderTrades();
}

function renderTrades() {
    const filtered = tradeFilter === "ALL"
        ? tradesData
        : tradesData.filter(t => t.action === tradeFilter);

    const tbody = document.getElementById("trades-body");
    if (!tbody) return;

    tbody.innerHTML = filtered.map(t => {
        const actionCls = t.action.toLowerCase();
        return `
            <tr>
                <td>${fmtDate(t.date)}</td>
                <td><span class="action-badge ${actionCls}">${t.action}</span></td>
                <td class="ticker">${t.ticker}</td>
                <td class="right">${t.shares}</td>
                <td class="right">${fmt$(t.price)}</td>
                <td class="right">${fmt$(t.value)}</td>
                <td>${t.reason || "—"}</td>
            </tr>
        `;
    }).join("");

    setContent("trades-count", `${filtered.length} trades`);
}

// ============================================================
// SYSTEM STATUS
// ============================================================
async function fetchStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        setContent("status-last-daily", data.last_daily || "Never");
        setContent("status-last-monthly", data.last_monthly || "Never");
        setContent("status-last-rebalance", data.last_rebalance || "Never");

        const nextReb = document.getElementById("status-next-rebalance");
        if (nextReb) {
            nextReb.textContent = data.next_rebalance || "—";
            nextReb.className = "status-value " + (data.rebalance_overdue ? "bad" : "ok");
        }

        setContent("status-days-since", data.days_since_rebalance != null ? `${data.days_since_rebalance} days` : "—");

        const stopsEl = document.getElementById("status-stops");
        if (stopsEl) {
            stopsEl.textContent = data.stops_coverage || "—";
            const [have, total] = (data.stops_coverage || "0/0").split("/").map(Number);
            stopsEl.className = "status-value " + (have === total && total > 0 ? "ok" : "bad");
        }

        setContent("status-positions", data.total_positions || "0");

        const logEl = document.getElementById("status-latest-log");
        if (logEl && data.latest_log) {
            logEl.textContent = data.latest_log.file;
            logEl.title = "Modified: " + data.latest_log.modified;
        } else if (logEl) {
            logEl.textContent = "No logs";
        }

        setContent("status-created", data.portfolio_created ? fmtDate(data.portfolio_created) : "—");

    } catch (e) {
        console.error("Status fetch error:", e);
    }
}

// ============================================================
// KELL CYCLE  (Oliver Kell — Cycle of Price Action)
// ============================================================
function kellWatchlist() {
    const el = document.getElementById("kell-tickers");
    return el ? el.value.trim() : "";
}

// Restore the saved watchlist and wire the Enter key
function initKell() {
    const el = document.getElementById("kell-tickers");
    if (!el) return;
    try { el.value = localStorage.getItem("kellWatchlist") || ""; } catch (e) {}
    el.addEventListener("keydown", ev => { if (ev.key === "Enter") loadKell(); });
}

// Called by the Analyze button / Enter — persists the watchlist, then fetches
function loadKell() {
    try { localStorage.setItem("kellWatchlist", kellWatchlist()); } catch (e) {}
    fetchKell();
}

async function fetchKell() {
    const tbody = document.getElementById("kell-body");
    try {
        const wl = kellWatchlist();
        const url = "/api/kell" + (wl ? "?tickers=" + encodeURIComponent(wl) : "");
        const res = await fetch(url);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        renderKell(data.results || []);
    } catch (e) {
        console.error("Kell fetch error:", e);
        if (tbody) tbody.innerHTML =
            '<tr><td colspan="10" class="error-msg">Failed to load Kell Cycle</td></tr>';
    }
}

// Ticker cell with the company name underneath (shared by both Kell cards)
function tickerCell(r) {
    const name = r.name ? `<div class="co-name">${r.name}</div>` : "";
    return `<td class="ticker">${r.ticker || "—"}${name}</td>`;
}

function renderKell(results) {
    const tbody = document.getElementById("kell-body");
    if (!tbody) return;

    if (!results.length) {
        tbody.innerHTML =
            '<tr><td colspan="10" class="loading">No positions yet — add watchlist tickers above to analyze them through Kell\'s lens.</td></tr>';
        return;
    }

    tbody.innerHTML = results.map(r => {
        const st = r.status || "gray";
        const stop = r.suggested_stop != null
            ? `${fmt$(r.suggested_stop)} <span style="color:var(--text-muted)">(${fmtPct(r.dist_to_stop)})</span>`
            : "—";
        return `
            <tr>
                ${tickerCell(r)}
                <td><span class="kell-badge ${st}">${r.phase || "—"}</span></td>
                <td class="kell-sig ${st}">${r.signal || "—"}</td>
                <td class="right">${fmt$(r.price)}</td>
                <td class="right">${fmtPct(r.ext_10ema)}</td>
                <td class="right">${fmtPct(r.ext_21ema)}</td>
                <td class="right">${fmtPct(r.ext_50sma)}</td>
                <td class="right ${plClass(r.rs_3m_vs_spy)}">${fmtPct(r.rs_3m_vs_spy)}</td>
                <td class="right">${stop}</td>
                <td class="kell-note">${r.thesis || r.note || ""}</td>
            </tr>
        `;
    }).join("");
}

// ============================================================
// KELL SCREENER  (whole-market scan)
// ============================================================
let kellScanTimer = null;

function fmtBig$(v) {
    if (v == null || isNaN(v)) return "—";
    if (v >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
    if (v >= 1e3) return "$" + (v / 1e3).toFixed(0) + "K";
    return "$" + v.toFixed(0);
}

async function fetchKellScan() {
    try {
        const res = await fetch("/api/kell/scan");
        const data = await res.json();
        renderKellScan(data);
        // Keep polling while a scan is running
        if (data.status && data.status.state === "running") {
            if (kellScanTimer) clearTimeout(kellScanTimer);
            kellScanTimer = setTimeout(fetchKellScan, 4000);
        }
    } catch (e) {
        console.error("Kell scan fetch error:", e);
    }
}

async function runKellScan() {
    const btn = document.getElementById("kell-scan-btn");
    try {
        if (btn) { btn.disabled = true; btn.textContent = "Scanning…"; }
        await fetch("/api/kell/scan/run", { method: "POST" });
    } catch (e) {
        console.error("Kell scan start error:", e);
    }
    setTimeout(fetchKellScan, 800);
}

function renderKellScan(data) {
    const status = data.status || { state: "idle" };
    const results = data.results || [];
    const btn = document.getElementById("kell-scan-btn");
    const statusEl = document.getElementById("kell-scan-status");
    const metaEl = document.getElementById("kell-scan-meta");
    const tbody = document.getElementById("kell-scan-body");

    const running = status.state === "running";
    if (btn) {
        btn.disabled = running;
        btn.textContent = running ? "Scanning…" : "Rescan market";
    }
    if (statusEl) {
        if (running && status.stage === "enriching") {
            statusEl.innerHTML =
                `<span class="loading-spinner"></span>Adding company details to ${status.matches || 0} setups…`;
        } else if (running) {
            statusEl.innerHTML =
                `<span class="loading-spinner"></span>Scanning ${status.scanned || 0}/${status.total || 0} — ${status.matches || 0} setups so far`;
        } else if (status.state === "error") {
            statusEl.textContent = "Scan error: " + (status.error || "unknown");
        } else if (data.generated) {
            statusEl.textContent = `Scanned ${data.scanned || 0} of ${data.universe || 0} names`;
        } else {
            statusEl.textContent = "No scan yet — click Rescan market (takes a few minutes).";
        }
    }
    if (metaEl) metaEl.textContent = data.generated ? "as of " + fmtTime(data.generated) : "—";

    if (!tbody) return;
    if (!results.length) {
        tbody.innerHTML = running
            ? '<tr><td colspan="9" class="loading">Scanning the market…</td></tr>'
            : '<tr><td colspan="9" class="loading">No buy setups in the last scan.</td></tr>';
        return;
    }
    tbody.innerHTML = results.map(r => {
        const st = r.status || "green";
        const stop = r.suggested_stop != null
            ? `${fmt$(r.suggested_stop)} <span style="color:var(--text-muted)">(${fmtPct(r.dist_to_stop)})</span>`
            : "—";
        return `
            <tr>
                ${tickerCell(r)}
                <td>${r.sector || "—"}</td>
                <td><span class="kell-badge ${st}">${r.phase}</span></td>
                <td class="kell-sig ${st}">${r.signal}</td>
                <td class="right">${fmt$(r.price)}</td>
                <td class="right ${plClass(r.rs_3m_vs_spy)}">${fmtPct(r.rs_3m_vs_spy)}</td>
                <td class="right">${fmtBig$(r.dollar_vol)}</td>
                <td class="right">${stop}</td>
                <td class="kell-note">${r.thesis || r.note || ""}</td>
            </tr>
        `;
    }).join("");
}
