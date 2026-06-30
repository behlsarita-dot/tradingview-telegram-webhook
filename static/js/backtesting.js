"use strict";

async function runBacktest() {
  const btn = document.getElementById("bt-btn");
  btn.disabled = true;
  btn.textContent = "Running...";

  document.getElementById("bt-loading").style.display = "block";
  document.getElementById("bt-results").style.display = "none";

  const payload = {
    symbol: document.getElementById("bt-symbol").value,
    timeframe: document.getElementById("bt-timeframe").value,
    start_date: document.getElementById("bt-start").value,
    end_date: document.getElementById("bt-end").value,
    parameters: {
      adx_threshold: parseInt(document.getElementById("bt-adx").value),
      min_confluence_score: parseInt(document.getElementById("bt-confluence").value),
      sl_pct: parseFloat(document.getElementById("bt-sl").value),
      tp_pct: parseFloat(document.getElementById("bt-tp").value),
    }
  };

  const res = await postApi("/api/backtest", payload);

  btn.disabled = false;
  btn.textContent = "Run Backtest";
  document.getElementById("bt-loading").style.display = "none";

  if (!res.success) {
    alert(`Backtest failed: ${res.error || "Unknown error"}`);
    return;
  }

  const r = res.results;

  document.getElementById("bt-total").textContent = r.total_trades;
  document.getElementById("bt-winrate").textContent = `${fmt(r.win_rate)}%`;
  document.getElementById("bt-pnl").innerHTML = fmtPnl(r.total_pnl);
  document.getElementById("bt-dd").textContent = `${fmt(r.max_drawdown_pct)}%`;

  document.getElementById("bt-detail").innerHTML = `
    <div class="grid-3">
      <div class="metric"><span class="metric-label">Winning Trades</span>
        <span class="metric-value green">${r.winning_trades}</span></div>
      <div class="metric"><span class="metric-label">Losing Trades</span>
        <span class="metric-value red">${r.losing_trades}</span></div>
      <div class="metric"><span class="metric-label">Profit Factor</span>
        <span class="metric-value">${fmt(r.profit_factor)}</span></div>
      <div class="metric"><span class="metric-label">Avg Win</span>
        <span class="metric-value green">Rs.${fmt(r.avg_win)}</span></div>
      <div class="metric"><span class="metric-label">Avg Loss</span>
        <span class="metric-value red">Rs.${fmt(r.avg_loss)}</span></div>
      <div class="metric"><span class="metric-label">Total Charges</span>
        <span class="metric-value">Rs.${fmt(r.total_charges)}</span></div>
      <div class="metric"><span class="metric-label">Best Trade</span>
        <span class="metric-value green">Rs.${fmt(r.best_trade)}</span></div>
      <div class="metric"><span class="metric-label">Worst Trade</span>
        <span class="metric-value red">Rs.${fmt(r.worst_trade)}</span></div>
      <div class="metric"><span class="metric-label">Avg Hold (bars)</span>
        <span class="metric-value">${fmt(r.avg_hold_bars, 1)}</span></div>
      <div class="metric"><span class="metric-label">Return</span>
        ${fmtPct(r.total_pnl_pct)}</div>
      <div class="metric"><span class="metric-label">Final Capital</span>
        <span class="metric-value">Rs.${fmt(r.final_capital)}</span></div>
    </div>
  `;

  if (res.trades && res.trades.length > 0) {
    const rows = res.trades.map(t => `
      <tr>
        <td>${t.entry_time ? t.entry_time.slice(0, 16) : "--"}</td>
        <td><span class="badge ${t.direction === "BUY" ? "badge-green" : "badge-red"}">${t.direction}</span></td>
        <td>Rs.${fmt(t.entry_price)}</td>
        <td>Rs.${fmt(t.exit_price)}</td>
        <td>${fmtPnl(t.net_pnl)}</td>
        <td class="text-muted">${t.exit_reason}</td>
        <td class="text-muted">${t.bars_held}</td>
      </tr>
    `).join("");

    document.getElementById("bt-trades").innerHTML = `
      <table class="trades-table">
        <thead>
          <tr>
            <th>Entry</th><th>Dir</th><th>Entry Price</th>
            <th>Exit Price</th><th>Net P&L</th><th>Reason</th><th>Bars</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  } else {
    document.getElementById("bt-trades").innerHTML =
      "<div class=\"empty-state\">No trades generated</div>";
  }

  document.getElementById("bt-results").style.display = "block";
}

loadMarketStatus();

document.getElementById("bt-end").value = new Date().toISOString().split("T")[0];
