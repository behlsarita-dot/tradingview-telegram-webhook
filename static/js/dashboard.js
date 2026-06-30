"use strict";

async function loadDashboard() {
  await Promise.all([
    loadPortfolio(),
    loadRisk(),
    loadPositions(),
    loadRecentTrades(),
    loadMarketStatus()
  ]);
}

async function loadPortfolio() {
  const res = await api("/api/portfolio");
  if (!res.success) return;

  setEl("capital", `Rs.${fmt(res.current_capital)}`);
  setEl("roi", `ROI: ${res.roi_pct >= 0 ? "+" : ""}${fmt(res.roi_pct)}%`);
  setEl("daily-pnl", fmtPnl(res.daily_pnl), true);
  setEl("trades-today", `Trades: ${res.total_trades || 0}`);
  setEl("total-pnl", fmtPnl(res.total_pnl), true);
  setEl("win-rate", `Win: ${fmt(res.win_rate)}%`);
  setEl("open-positions", res.open_positions || 0);
}

async function loadRisk() {
  const res = await api("/api/risk/report");
  if (!res.success) return;

  setEl("drawdown", `${fmt(res.current_drawdown)}%`);
  setEl("consec-losses", res.consecutive_losses);
  setEl("size-mult", `${fmt(res.position_size_multiplier)}x`);
  setEl("heat", `Heat: ${fmt(res.portfolio_heat)}%`);

  const badge = document.getElementById("trading-status");
  const cbMsg = document.getElementById("circuit-breaker-msg");

  if (res.can_trade) {
    badge.textContent = "ACTIVE";
    badge.className = "badge badge-green";
    cbMsg.style.display = "none";
  } else {
    badge.textContent = "HALTED";
    badge.className = "badge badge-red";
    cbMsg.style.display = "block";
    cbMsg.textContent = res.circuit_breaker_reason;
  }

  if (res.recovery_mode) {
    badge.textContent = "RECOVERY";
    badge.className = "badge badge-yellow";
  }
}

async function loadPositions() {
  const res = await api("/api/positions?status=OPEN&limit=20");
  const cont = document.getElementById("positions-container");

  if (!res.success || !res.positions || res.positions.length === 0) {
    cont.innerHTML = "<div class=\"empty-state\">No open positions</div>";
    return;
  }

  cont.innerHTML = res.positions.map(p => {
    const isBuy = ["BUY", "LONG"].includes(p.action);
    return `
      <div class="position-row">
        <div class="pos-symbol ${isBuy ? "green" : "red"}">${p.symbol}</div>
        <div>
          <span class="badge ${isBuy ? "badge-green" : "badge-red"}">${p.action}</span>
        </div>
        <div class="pos-detail">
          Entry Rs.${fmt(p.entry_price)} | Qty ${p.quantity}
        </div>
        <div class="pos-detail">
          SL ${p.stop_loss ? "Rs." + fmt(p.stop_loss) : "--"} |
          TP ${p.take_profit ? "Rs." + fmt(p.take_profit) : "--"}
        </div>
        <div class="pos-pnl">${fmtPnl(p.unrealized_pnl)}</div>
        <div class="pos-time">${timeAgo(p.entry_time)}</div>
      </div>
    `;
  }).join("");
}

async function loadRecentTrades() {
  const res = await api("/api/positions?status=CLOSED&limit=10");
  const cont = document.getElementById("trades-container");

  if (!res.success || !res.positions || res.positions.length === 0) {
    cont.innerHTML = "<div class=\"empty-state\">No closed trades yet</div>";
    return;
  }

  const rows = res.positions.map(p => `
    <tr>
      <td>${p.symbol}</td>
      <td><span class="badge ${p.action === "BUY" ? "badge-green" : "badge-red"}">${p.action}</span></td>
      <td>Rs.${fmt(p.entry_price)}</td>
      <td>Rs.${fmt(p.exit_price)}</td>
      <td>${fmtPnl(p.pnl)}</td>
      <td class="text-muted">${p.exit_reason || "--"}</td>
      <td class="text-muted">${timeAgo(p.exit_time)}</td>
    </tr>
  `).join("");

  cont.innerHTML = `
    <table class="trades-table">
      <thead>
        <tr>
          <th>Symbol</th><th>Action</th><th>Entry</th>
          <th>Exit</th><th>P&L</th><th>Reason</th><th>When</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function setEl(id, value, asHtml = false) {
  const el = document.getElementById(id);
  if (!el) return;
  if (asHtml) el.innerHTML = value;
  else el.textContent = value;
}

startPolling(loadDashboard, 30000);
