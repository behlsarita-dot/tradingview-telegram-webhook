"use strict";

async function loadDashboard() {
  await Promise.all([
    loadPortfolio(),
    loadRisk(),
    loadPositions(),
    loadRecentTrades(),
    loadMarketStatus(),
    loadKillSwitch()
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

  if (res.kill_switch_active) {
    badge.textContent = "HALTED";
    badge.className = "badge badge-red";
    cbMsg.style.display = "block";
    cbMsg.textContent = "Kill switch active: " + (res.circuit_breaker_reason || "Trading manually halted");
  } else if (!res.can_trade) {
    badge.textContent = "HALTED";
    badge.className = "badge badge-red";
    cbMsg.style.display = "block";
    cbMsg.textContent = res.circuit_breaker_reason || "Circuit breaker active";
  } else if (res.recovery_mode) {
    badge.textContent = "RECOVERY";
    badge.className = "badge badge-yellow";
    cbMsg.style.display = "none";
  } else {
    badge.textContent = "ACTIVE";
    badge.className = "badge badge-green";
    cbMsg.style.display = "none";
  }
}

async function loadKillSwitch() {
  const res = await api("/api/kill-switch");
  if (!res.success) return;
  const btn = document.getElementById("kill-switch-btn");
  if (res.trading_enabled) {
    btn.textContent = "Halt Trading";
    btn.className = "btn btn-sm btn-danger";
  } else {
    btn.textContent = "Resume Trading";
    btn.className = "btn btn-sm";
    btn.style.background = "#22c55e";
  }
}

async function toggleKillSwitch() {
  const current = await api("/api/kill-switch");
  const willEnable = !current.trading_enabled;
  const action = willEnable ? "RESUME" : "HALT";
  const secret = prompt(`Enter webhook secret to ${action} trading:`);
  if (!secret) return;
  const reason = willEnable ? "" : (prompt("Reason for halting (optional):") || "Manual halt");
  const res = await postApi("/api/kill-switch", {
    webhook_secret: secret,
    enabled: willEnable,
    reason: reason
  });
  if (res.success) {
    alert(willEnable ? "Trading RESUMED." : "Trading HALTED. No new positions will open.");
    loadDashboard();
  } else {
    alert("Failed: " + (res.message || "Check webhook secret"));
  }
}

async function emergencyCloseAll() {
  const sure = confirm("This will IMMEDIATELY close ALL open positions and halt trading. This cannot be undone. Continue?");
  if (!sure) return;

  const secret = prompt("Enter webhook secret to confirm EMERGENCY CLOSE ALL:");
  if (!secret) return;

  const priceInput = prompt(
    "Exit price to close positions at (leave blank to close at entry price with zero P&L):"
  );
  const reason = prompt("Reason for emergency close:") || "Manual emergency close";

  const body = { webhook_secret: secret, reason: reason };
  if (priceInput && priceInput.trim() !== "") {
    body.exit_price = parseFloat(priceInput);
  }

  const res = await postApi("/api/emergency-close", body);

  if (res.success) {
    alert(`Closed ${res.closed_count} position(s). Trading is now halted.`);
    loadDashboard();
  } else {
    alert("Failed: " + (res.message || "Check webhook secret"));
  }
}

async function closePosition(symbol) {
  const price = prompt(`Enter current price to close ${symbol} position:`);
  if (!price || isNaN(parseFloat(price))) return;

  const secret = prompt("Enter webhook secret to confirm close:");
  if (!secret) return;

  const res = await postApi("/api/webhook", {
    webhook_secret: secret,
    symbol: symbol,
    action: "EXIT",
    price: parseFloat(price),
    exit_reason: "Manual close"
  });

  if (res.success) {
    alert(`Closed ${symbol} at Rs.${price}. P&L: Rs.${res.net_pnl}`);
    loadDashboard();
  } else {
    alert("Failed: " + (res.message || "check price/secret"));
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
        <div><span class="badge ${isBuy ? "badge-green" : "badge-red"}">${p.action}</span></div>
        <div class="pos-detail">Entry Rs.${fmt(p.entry_price)} | Qty ${p.quantity}</div>
        <div class="pos-detail">
          SL ${p.stop_loss ? "Rs." + fmt(p.stop_loss) : "--"} |
          TP ${p.take_profit ? "Rs." + fmt(p.take_profit) : "--"}
        </div>
        <div class="pos-pnl">${fmtPnl(p.unrealized_pnl)}</div>
        <div class="pos-time">${timeAgo(p.entry_time)}</div>
        <button onclick="closePosition('${p.symbol}')" class="btn btn-sm btn-danger">Close</button>
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
        <tr><th>Symbol</th><th>Action</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th><th>When</th></tr>
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
