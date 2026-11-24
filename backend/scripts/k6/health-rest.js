import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { Trend, Rate } from 'k6/metrics';

const fixture = JSON.parse(open(__ENV.FIXTURE || '/fixture/fixture.json'));
const base = __ENV.BASE_URL || 'http://host.docker.internal:18064';
if (!/^http:\/\/(127\.0\.0\.1|localhost|host\.docker\.internal):18064$/.test(base)) {
  throw new Error('This introductory script only targets the isolated local server on port 18064.');
}
const vus = Number(__ENV.VUS || 1);
if (![1, 5, 10].includes(vus) || fixture.tokens.length < vus) throw new Error('Use 1, 5, or 10 VUs with distinct synthetic members.');
const smoke = __ENV.MODE === 'smoke';
const latency = new Trend('measured_duration', true);
const failures = new Rate('measured_failure');
const readiness = __ENV.READINESS === 'True';
const cases = readiness ? [
  {name: 'new_steps', path: 'metrics/steps', days: 7},
  {name: 'new_timeline', path: 'timeline', days: 7},
  {name: 'new_score', path: 'lifestyle-score'},
  {name: 'new_message', path: 'daily-message'},
] : [
  {name: 'steps_7d', path: 'metrics/steps', days: 7},
  {name: 'steps_366d', path: 'metrics/steps', days: 366},
  {name: 'timeline_7d', path: 'timeline', days: 7},
  {name: 'timeline_31d', path: 'timeline', days: 31},
];
const thresholds = { checks: ['rate==1'] };
for (const c of cases) {
  // No latency SLO has been agreed. >=0 only exposes per-case timing in the summary.
  thresholds[`measured_duration{endpoint:${c.name}}`] = ['p(95)>=0'];
  thresholds[`measured_failure{endpoint:${c.name}}`] = [{threshold: 'rate==0', abortOnFail: true, delayAbortEval: '10s'}];
}
export const options = {
  scenarios: { health: smoke
    ? {executor: 'per-vu-iterations', vus: 1, iterations: 1, maxDuration: '30s'}
    : {executor: 'constant-vus', vus, duration: __ENV.DURATION || '60s', gracefulStop: '10s'} },
  thresholds,
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'max', 'count'],
};

function startDate(days) {
  const date = new Date(`${fixture.end}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - days + 1);
  return date.toISOString().slice(0, 10);
}

function request(c, member, measured) {
  const start = c.days ? startDate(c.days) : null;
  const query = c.days ? `?start=${start}&end=${fixture.end}` : "";
  const response = http.get(`${base}/api/v1/health/${c.path}${query}`, {
    headers: {Authorization: `Bearer ${(readiness ? fixture.new_tokens : fixture.tokens)[member]}`},
    redirects: 0, timeout: '5s', tags: {name: c.name, phase: measured ? 'measurement' : 'warmup'},
  });
  let valid = false;
  try {
    const body = response.json();
    const data = body.data;
    if (readiness) {
      valid = response.status === 503 && body.error?.code === "HEALTH_DATA_UNAVAILABLE" && body.error?.context?.reason === "DATA_NOT_READY";
    } else {
      valid = response.status === 200 && data && data.period.start === start &&
      data.period.end === fixture.end && data.days.length === c.days &&
      (c.path === 'metrics/steps'
        ? data.metric === 'steps' && data.summary.avg === (250 + member) * 24 &&
          data.summary.total === (250 + member) * 24 * c.days &&
          data.days.every(d => d.value === (250 + member) * 24 && d.missing === false)
        : data.days.every(d => d.hourlySteps?.length === 24 &&
          d.hourlySteps.every(v => v === 250 + member) &&
          d.hourlyHeartRates?.length === 24 &&
          d.hourlyHeartRates.every(h => h.avg === 65 + member && h.sampleCount === 60) &&
          d.mainSleep != null && Array.isArray(d.sleepSegments) && d.exerciseSegments.length === 1));
    }
  } catch (_) {
    valid = false;
  }
  if (measured) {
    check(response, {[`${c.name}: status and synthetic data match`]: () => Boolean(valid)});
    latency.add(response.timings.duration, {endpoint: c.name});
    failures.add(!valid, {endpoint: c.name});
  }
  return Boolean(valid);
}

export function setup() {
  // Warm each member/case once, outside custom measurement metrics; never print tokens/bodies.
  for (let member = 0; member < vus; member++) {
    for (const c of cases) if (!request(c, member, false)) {
      throw new Error(`Preflight failed: ${c.name}, synthetic member ${member}. Stop and inspect locally.`);
    }
  }
}

export default function () {
  const member = exec.vu.idInTest - 1;
  for (const c of cases) request(c, member, true);
  sleep(1);
}

export function handleSummary(data) {
  const output = {};
  for (const [name, metric] of Object.entries(data.metrics)) {
    if (name.startsWith('measured_') || name === 'checks' || name === 'iterations') output[name] = metric.values;
  }
  return {
    [`/results/${__ENV.RUN_NAME || 'latest'}.json`]: JSON.stringify(data, null, 2),
    stdout: JSON.stringify({vus, smoke, readiness, metrics: output}, null, 2) + '\n',
  };
}
