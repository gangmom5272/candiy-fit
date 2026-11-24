// Synthetic-only subset of data/docs/candiy-api.md, for backend sync integration.
const http = require('http');
const metrics = new Set([
  'steps', 'heart_rates', 'oxygen_saturations', 'sleeps', 'exercise_sessions',
  'calories_burned', 'distances', 'speeds', 'vo2_maxes', 'weights', 'body_fats',
  'basal_metabolic_rates', 'heights'
]);
let state = { mode: 'default', owner: '', day: '', calls: [] };
function send(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}
http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (req.method === 'POST' && url.pathname === '/__control') {
    // Also runnable with Node when the Postman dataset engine is unavailable.
    let body = req.body;
    if (!body) {
      let raw = '';
      for await (const chunk of req) raw += chunk;
      try { body = JSON.parse(raw); } catch { return send(res, 400, { error: 'invalid JSON' }); }
    }
    if (!['default', 'steps-fail', 'all-404', 'steps-404', 'mixed-404', 'empty', 'auth-fail', 'slow',
          'token-invalid-json', 'token-missing', 'token-bad-expiry'].includes(body.mode)) {
      return send(res, 400, { error: 'invalid synthetic scenario' });
    }
    state = { ...body, calls: [] };
    return send(res, 200, { ok: true });
  }
  if (req.method === 'GET' && url.pathname === '/__calls') {
    return send(res, 200, state.calls);
  }
  if (req.method === 'POST' && url.pathname === '/oauth/token') {
    state.calls.push({ route: 'token' });
    if (state.mode === 'auth-fail' || req.headers.authorization !==
        'Basic ' + Buffer.from('synthetic-client:synthetic-secret').toString('base64')) {
      return send(res, 401, { error: 'invalid_client' });
    }
    if (state.mode === 'token-invalid-json') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end('synthetic-secret-not-json');
    }
    if (state.mode === 'token-missing') return send(res, 200, {});
    if (state.mode === 'token-bad-expiry') {
      return send(res, 200, { access_token: 'synthetic-secret', expires_in: 'invalid' });
    }
    return send(res, 200, { access_token: 'synthetic-access', token_type: 'Bearer', expires_in: 7200 });
  }
  const metric = url.pathname.startsWith('/api/') ? url.pathname.slice(5) : '';
  if (req.method !== 'GET' || !metrics.has(metric)) {
    return send(res, 501, { error: 'not modeled' });
  }
  if (req.headers.authorization !== 'Bearer synthetic-access') {
    return send(res, 401, { status: 'fail', data: { error: 'Not authorized' } });
  }
  if (!url.searchParams.get('start') ||
      (metric === 'oxygen_saturations' && url.searchParams.has('end'))) {
    return send(res, 400, { status: 'fail', data: { error: 'invalid period' } });
  }
  state.calls.push({ route: metric, owner: url.searchParams.get('end_user_id') });
  if (state.mode === 'slow') await new Promise(resolve => setTimeout(resolve, state.delayMs || 100));
  if (state.mode === 'all-404' ||
      (state.mode === 'steps-404' && metric === 'steps') ||
      (state.mode === 'mixed-404' && metric !== 'steps') ||
      url.searchParams.get('end_user_id') !== state.owner) {
    return send(res, 404, { status: 'fail', data: { error: 'Not found' } });
  }
  if (['steps-fail', 'mixed-404'].includes(state.mode) && metric === 'steps') {
    return send(res, 503, { status: 'fail', data: { error: 'unavailable' } });
  }
  const data = [];
  if (state.mode !== 'empty' && metric === 'steps' && url.searchParams.get('start') <= state.day &&
      (!url.searchParams.get('end') || state.day <= url.searchParams.get('end'))) {
    data.push({
      app: 'com.sec.android.app.shealth', data_id: 'sync223-' + state.owner,
      start: state.day + 'T00:00:00+09:00', end: state.day + 'T23:59:59+09:00',
      data: JSON.stringify({ count: 8000 }),
      last_modified_time: state.day + 'T23:59:59+09:00',
      created_at: state.day + 'T23:59:59+09:00', updated_at: state.day + 'T23:59:59+09:00'
    });
  }
  return send(res, 200, { status: 'success', data });
}).listen(process.env.PORT || 40123, '127.0.0.1', () => {
  console.log('Synthetic CANDiY HTTP fixture ready');
});
