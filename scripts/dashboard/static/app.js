/* portfolio-manager dashboard — vanilla JS, no build. */

const $ = (sel) => document.querySelector(sel);

let equityChart = null;

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function fmtUsd(v, digits = 2) {
  if (v == null || isNaN(v)) return '—';
  return v.toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

function fmtPct(v, digits = 2) {
  if (v == null || isNaN(v)) return '—';
  return `${v.toFixed(digits)}%`;
}

function fmtNum(v, digits = 4) {
  if (v == null || isNaN(v)) return '—';
  return v.toFixed(digits);
}

function colorFor(v) {
  if (v == null || isNaN(v)) return 'neutral';
  if (v > 0) return 'positive';
  if (v < 0) return 'negative';
  return 'neutral';
}

function fmtTimestamp(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch { return iso; }
}

function renderState(state) {
  const last = state.last_cycle;
  if (!last) {
    $('#metric-equity').textContent = '—';
    $('#metric-dd').textContent = '—';
    $('#metric-mode').textContent = '—';
    $('#wallet-label').textContent = `wallet: ${state.wallet || '—'}`;
    return;
  }
  const pos = last.positions || {};
  $('#metric-equity').textContent = '$' + fmtUsd(pos.total_equity_usd);
  $('#metric-equity').className = 'card-value ' + colorFor(pos.total_equity_usd > 0 ? 1 : 0);
  const dd = pos.drawdown_from_hwm_pct ?? 0;
  $('#metric-dd').textContent = fmtPct(dd);
  $('#metric-dd').className = 'card-value ' + (dd > 0 ? 'negative' : 'neutral');
  $('#metric-mode').textContent = last.mode || '—';
  $('#metric-mode').className = 'card-value neutral';
  $('#wallet-label').textContent = `wallet: ${last.wallet || state.wallet || '—'}`;
  $('#updated-label').textContent = `last update: ${fmtTimestamp(last.ts_utc)}`;
}

function renderMetrics(m) {
  const summary = m.metrics || {};
  $('#metric-return').textContent = fmtPct(summary.total_return_pct);
  $('#metric-return').className = 'card-value ' + colorFor(summary.total_return_pct);
  $('#metric-sharpe').textContent = fmtNum(summary.sharpe);
  $('#metric-sharpe').className = 'card-value ' + colorFor(summary.sharpe);
  $('#metric-cycles').textContent = m.cycle_count ?? '—';
}

function renderEquity(equity) {
  const ctx = $('#equity-chart').getContext('2d');
  const labels = equity.series.map((p) => new Date(p.ts));
  const values = equity.series.map((p) => p.equity_usd);

  if (equityChart) {
    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = values;
    equityChart.update('none');
    return;
  }
  equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Equity (USD)',
        data: values,
        borderColor: '#4ade80',
        backgroundColor: 'rgba(74, 222, 128, 0.1)',
        borderWidth: 1.4,
        fill: true,
        tension: 0,
        pointRadius: 0,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      scales: {
        x: { type: 'time', time: { tooltipFormat: 'PP HH:mm' },
             grid: { color: '#242837' }, ticks: { color: '#94a3b8', font: { size: 10 } } },
        y: { grid: { color: '#242837' }, ticks: { color: '#94a3b8', font: { size: 10 } } },
      },
      plugins: { legend: { display: false } },
    },
  });
}

function renderPositions(state) {
  const tbody = $('#positions-table tbody');
  const last = state.last_cycle;
  // The audit's positions is a summary, not the full positions list. For now
  // we surface the summary fields; full positions live in PM's sqlite (read
  // via /api/state if/when wallet is set).
  if (!last || !last.positions) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">no audit yet</td></tr>';
    return;
  }
  const p = last.positions;
  const rows = [];
  rows.push(`<tr>
    <td colspan="3">portfolio</td>
    <td class="numeric">$${fmtUsd(p.total_equity_usd)}</td>
    <td class="numeric">—</td>
    <td class="numeric">—</td>
    <td class="numeric">${fmtPct(p.drawdown_from_hwm_pct)}</td>
  </tr>`);
  if (p.n_positions === 0) {
    rows.push(`<tr><td colspan="7" class="empty">${p.n_positions} positions held</td></tr>`);
  } else {
    rows.push(`<tr><td colspan="7" class="empty">${p.n_positions} position(s) held — see /api/state for detail</td></tr>`);
  }
  tbody.innerHTML = rows.join('');
}

function renderAlerts(alertsResp) {
  $('#alerts-count').textContent = alertsResp.count || 0;
  const ul = $('#alerts-list');
  if (!alertsResp.alerts || alertsResp.alerts.length === 0) {
    ul.innerHTML = '<li class="empty">no pending alerts</li>';
    return;
  }
  ul.innerHTML = alertsResp.alerts.slice(0, 30).map((a) => {
    const d = a.decision || {};
    return `<li>
      <div class="alert-head">
        <span class="alert-rule">${a.rule_id || '?'}</span>
        <span class="sev-${a.severity} alert-meta">[${a.severity}]</span>
      </div>
      <div class="alert-reason">${d.action || ''} ${d.asset || ''} — ${d.reason || ''}</div>
      <div class="alert-meta">${fmtTimestamp(a.created_at_utc)}  ·  ${a.alert_id?.slice(0, 8) || ''}</div>
    </li>`;
  }).join('');
}

function renderAudit(audit) {
  const tbody = $('#audit-table tbody');
  if (!audit.rows || audit.rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">no audit rows yet</td></tr>';
    return;
  }
  tbody.innerHTML = audit.rows.slice(0, 50).map((r) => {
    const sa = (r.strategy?.actions || []).length;
    const rd = (r.decisions || []).length;
    const desc = sa > 0 || rd > 0
      ? `${sa} strategy / ${rd} rule`
      : (r.event === 'watch.cycle' ? '—' : (r.event || ''));
    const fills = (r.fills || []).length;
    const eq = r.positions?.total_equity_usd;
    return `<tr>
      <td>${fmtTimestamp(r.ts_utc)}</td>
      <td>${r.event || '?'}</td>
      <td>${r.wallet || '—'}</td>
      <td>${desc}</td>
      <td class="numeric">${fills}</td>
      <td class="numeric">${eq != null ? '$' + fmtUsd(eq) : '—'}</td>
    </tr>`;
  }).join('');
}

async function refresh() {
  try {
    const snap = await fetchJSON('/api/snapshot');
    renderState(snap.state);
    renderMetrics(snap.metrics);
    renderAudit(snap.audit);
    renderAlerts(snap.alerts_pending);
    renderPositions(snap.state);
    const equity = await fetchJSON('/api/equity');
    renderEquity(equity);
  } catch (e) {
    console.error('refresh failed', e);
  }
}

function startSSE() {
  const es = new EventSource('/events');
  es.addEventListener('cycle', () => refresh());
  es.addEventListener('alert', () => refresh());
  es.addEventListener('hello', () => { $('#conn-dot').classList.remove('disconnected'); });
  es.addEventListener('ping', () => {});
  es.onerror = () => {
    $('#conn-dot').classList.add('disconnected');
    // EventSource auto-reconnects. Re-render the dot when it does.
  };
  es.onopen = () => $('#conn-dot').classList.remove('disconnected');
}

document.addEventListener('DOMContentLoaded', () => {
  refresh();
  startSSE();
  // Polling fallback in case SSE silently fails.
  setInterval(refresh, 10000);
});
