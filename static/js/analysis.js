"use strict";

async function runAnalysis() {
  const symbol = document.getElementById("symbol-select").value;
  const result = document.getElementById("analysis-result");
  result.innerHTML = "<div class=\"empty-state\">Fetching analysis...</div>";

  document.getElementById("confluence-card").style.display = "none";
  document.getElementById("levels-card").style.display = "none";

  const res = await api(`/api/analyze/${symbol}`);

  if (!res.success) {
    result.innerHTML = `<div class="alert alert-danger">${res.message || "Analysis failed"}</div>`;
    return;
  }

  const signalClass = res.signal === "BUY" ? "green" :
                      res.signal === "SELL" ? "red" : "yellow";

  const qualityClass = res.quality === "HIGH" ? "badge-green" :
                       res.quality === "MEDIUM" ? "badge-yellow" : "badge-red";

  result.innerHTML = `
    <div class="grid-4">
      <div class="stat-card">
        <div class="stat-label">Signal</div>
        <div class="stat-value ${signalClass}">${res.signal || "NONE"}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Quality</div>
        <div class="stat-value">
          <span class="badge ${qualityClass}">${res.quality}</span>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Confluence</div>
        <div class="stat-value">${res.confluence_score} / ${res.confluence_max || 5}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">ADX</div>
        <div class="stat-value">${fmt(res.adx)}</div>
      </div>
    </div>
    ${res.fallback ? "<div class=\"alert alert-warning mt-8\">Live data unavailable - using fallback</div>" : ""}
  `;

  if (res.reasons && res.reasons.length > 0) {
    const card = document.getElementById("confluence-card");
    card.style.display = "block";
    document.getElementById("confluence-factors").innerHTML =
      res.reasons.map(r => `
        <div class="factor-row">
          <span class="factor-text">${r}</span>
        </div>
      `).join("");
  }

  if (res.signal !== "NONE" && res.entry_price) {
    document.getElementById("levels-card").style.display = "block";
    document.getElementById("level-entry").textContent = `Rs.${fmt(res.entry_price)}`;
    document.getElementById("level-sl").textContent = res.stop_loss ? `Rs.${fmt(res.stop_loss)}` : "--";
    document.getElementById("level-tp").textContent = res.take_profit ? `Rs.${fmt(res.take_profit)}` : "--";
  }
}

async function loadMarketDetail() {
  const res = await api("/api/market/status");
  const el = document.getElementById("market-detail");
  if (!el) return;
  el.innerHTML = `
    <div class="grid-3">
      <div class="metric">
        <span class="metric-label">Status</span>
        <span class="metric-value ${res.status === "OPEN" ? "green" : "red"}">${res.status}</span>
      </div>
      <div class="metric">
        <span class="metric-label">Current IST</span>
        <span class="metric-value">${res.current_time_ist}</span>
      </div>
      <div class="metric">
        <span class="metric-label">Session</span>
        <span class="metric-value">${res.opens_at} - ${res.closes_at}</span>
      </div>
    </div>
  `;
}

loadMarketStatus();
loadMarketDetail();
startPolling(loadMarketDetail, 60000);
