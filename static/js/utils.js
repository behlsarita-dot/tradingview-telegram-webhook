"use strict";

async function api(endpoint) {
  try {
    const res = await fetch(endpoint);
    return await res.json();
  } catch (e) {
    console.error("API error:", endpoint, e);
    return { success: false, error: e.message };
  }
}

async function postApi(endpoint, body) {
  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    return await res.json();
  } catch (e) {
    console.error("POST error:", endpoint, e);
    return { success: false, error: e.message };
  }
}

function fmt(val, decimals = 2) {
  if (val == null || isNaN(val)) return "--";
  return parseFloat(val).toLocaleString("en-IN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  });
}

function fmtCurrency(val) {
  if (val == null || isNaN(val)) return "Rs.--";
  const n = parseFloat(val);
  return (n >= 0 ? "Rs." : "-Rs.") + Math.abs(n).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

function fmtPnl(val) {
  if (val == null || isNaN(val)) return "<span>--</span>";
  const n = parseFloat(val);
  const cls = n >= 0 ? "green" : "red";
  const sign = n >= 0 ? "+" : "";
  return `<span class="${cls}">${sign}Rs.${Math.abs(n).toLocaleString("en-IN", { minimumFractionDigits: 2 })}</span>`;
}

function fmtPct(val) {
  if (val == null || isNaN(val)) return "--%";
  const n = parseFloat(val);
  const cls = n >= 0 ? "green" : "red";
  return `<span class="${cls}">${n >= 0 ? "+" : ""}${n.toFixed(2)}%</span>`;
}

function timeAgo(isoStr) {
  if (!isoStr) return "--";
  const diff = Date.now() - new Date(isoStr).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

async function loadMarketStatus() {
  const res = await api("/api/market/status");
  const el = document.getElementById("market-status");
  if (!el) return;
  el.textContent = res.status || "--";
  el.className = `market-badge ${res.status === "OPEN" ? "open" : "closed"}`;
}

function startPolling(fn, intervalMs = 30000) {
  fn();
  return setInterval(fn, intervalMs);
}
