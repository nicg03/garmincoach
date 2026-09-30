/* Chart.js setup shared by every view. Charts are tracked so a route change
   destroys them instead of leaking canvases. */

export const COLORS = {
  load: 'rgba(33, 84, 255, .22)',
  atl: '#2154ff',
  ctl: '#0f8a5a',
  form: '#7c5cff',
  hrv: '#0e9aa0',
  rhr: '#d63b3b',
  score: '#8d8d86',
  deep: '#1b3a7a',
  light: '#8aa8ff',
  rem: '#0e9aa0',
};

export const PALETTE = ['#2f6df6', '#12a5a5', '#e08c2c', '#7c5cd6', '#1f9d5b', '#d64545'];

const charts = new Map();

export function css(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}

export function initCharts() {
  if (!window.Chart) return;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = css('--muted');
}

export function axes() {
  const grid = css('--line');
  const tick = css('--muted');
  const ticks = { color: tick, font: { size: 10 } };
  const title = (text) => ({ display: true, text, color: tick, font: { size: 10 } });
  return {
    grid,
    tick,
    title,
    x: { grid: { display: false }, ticks: { ...ticks, maxRotation: 0, autoSkipPadding: 18 } },
    y: { grid: { color: grid }, border: { display: false }, ticks },
    right: { position: 'right', grid: { display: false }, border: { display: false }, ticks },
  };
}

export function baseOptions(extra = {}) {
  const a = axes();
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { labels: { color: a.tick, boxWidth: 10, usePointStyle: true, font: { size: 11 } } },
      tooltip: { backgroundColor: '#1c2530', padding: 10, cornerRadius: 8 },
    },
    scales: { x: a.x, y: a.y },
    ...extra,
  };
}

export function draw(canvas, config) {
  if (!window.Chart || !canvas) return null;
  const key = canvas.id || canvas;
  if (charts.has(key)) charts.get(key).destroy();
  const chart = new Chart(canvas, config);
  charts.set(key, chart);
  return chart;
}

export function destroyCharts() {
  charts.forEach((chart) => chart.destroy());
  charts.clear();
}
