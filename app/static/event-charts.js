async function fetchJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `Request failed: ${resp.status}`);
  }
  return resp.json();
}

async function renderMissDistanceChart(eventId, updateId) {
  const canvas = document.getElementById('missDistanceChart');
  if (!canvas) return;

  const data = await fetchJson(`/events/${eventId}/series?update_id=${updateId}`);
  const points = (data.times || []).map((t, i) => ({ x: t, y: data.miss_distance_km[i] }));

  // eslint-disable-next-line no-undef
  new Chart(canvas, {
    type: 'line',
    data: {
      datasets: [
        {
          label: 'Miss distance (km)',
          data: points,
          borderColor: '#4e79a7',
          backgroundColor: 'rgba(78,121,167,0.1)',
          pointRadius: 0,
          tension: 0.2,
        },
      ],
    },
    options: {
      responsive: true,
      parsing: false,
      scales: {
        x: { type: 'category', ticks: { maxTicksLimit: 6 } },
        y: { title: { display: true, text: 'km' } },
      },
    },
  });
}

async function renderRtnCharts(eventId, updateId) {
  const canvasRT = document.getElementById('rtnRTChart');
  const canvasN = document.getElementById('rtnNChart');
  if (!canvasRT && !canvasN) return;

  const data = await fetchJson(`/events/${eventId}/rtn-series?update_id=${updateId}`);
  if (!data.times || !data.times.length) return;
  const pointsRT = (data.r || []).map((rv, i) => ({ x: data.t[i], y: rv }));

  if (canvasRT) {
    // eslint-disable-next-line no-undef
    new Chart(canvasRT, {
      type: 'line',
      data: {
        datasets: [
          {
            label: 'R vs T (km)',
            data: pointsRT,
            borderColor: '#59a14f',
            pointRadius: 0,
            tension: 0.2,
          },
        ],
      },
      options: {
        responsive: true,
        parsing: false,
        scales: {
          x: { title: { display: true, text: 'T (km)' } },
          y: { title: { display: true, text: 'R (km)' } },
        },
      },
    });
  }

  if (canvasN) {
    const pointsN = (data.times || []).map((t, i) => ({ x: t, y: data.n[i] }));
    // eslint-disable-next-line no-undef
    new Chart(canvasN, {
      type: 'line',
      data: {
        datasets: [
          {
            label: 'N vs time (km)',
            data: pointsN,
            borderColor: '#f28e2b',
            pointRadius: 0,
            tension: 0.2,
          },
        ],
      },
      options: {
        responsive: true,
      parsing: false,
      scales: {
        x: { type: 'category', ticks: { maxTicksLimit: 6 } },
        y: { title: { display: true, text: 'km' } },
      },
    },
  });
  }
}

async function renderEvolutionChart(eventId) {
  const canvas = document.getElementById('evolutionChart');
  if (!canvas) return;

  const data = await fetchJson(`/events/${eventId}`);
  const updates = (data.updates || []).slice().reverse();
  const points = updates.map((u) => ({ x: u.computed_at, y: u.miss_distance_km }));

  // eslint-disable-next-line no-undef
  new Chart(canvas, {
    type: 'line',
    data: {
      datasets: [
        {
          label: 'Miss distance @TCA (km)',
          data: points,
          borderColor: '#9c755f',
          pointRadius: 2,
          tension: 0.2,
        },
      ],
    },
    options: {
      responsive: true,
      parsing: false,
      scales: {
        x: { type: 'category', ticks: { maxTicksLimit: 6 } },
        y: { title: { display: true, text: 'km' } },
      },
    },
  });
}

window.renderEventCharts = async function renderEventCharts(eventId, updateId) {
  try {
    await renderMissDistanceChart(eventId, updateId);
    await renderRtnCharts(eventId, updateId);
    await renderEvolutionChart(eventId);
  } catch (err) {
    // Charts are non-critical; swallow errors.
    // eslint-disable-next-line no-console
    console.warn('Failed to render charts', err);
  }
};
