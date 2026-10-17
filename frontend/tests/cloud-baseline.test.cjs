const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'lib', 'cloudApi.ts'), 'utf8')
  .replaceAll('import.meta.env', 'globalThis.__cloudTestEnv');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

test('F0 comparison recovers a verified score when its progress event is missing', async () => {
  const originalFetch = global.fetch;
  global.__cloudTestEnv = { VITE_OTHELLO_API_URL: 'https://api.example.test' };
  const module = { exports: {} };
  new Function('require', 'module', 'exports', compiled)(() => { throw new Error('Unexpected import'); }, module, module.exports);
  const jobId = 'a'.repeat(32);
  const baseline = { plan_id: 'P', units: 'm', hubs: [{ hub_id: 'H1' }], reported_metrics: { hub_count: 1, total_length_m: 42 } };
  const requests = [];
  global.fetch = async (url, init) => {
    const pathname = new URL(url).pathname;
    requests.push(`${init?.method ?? 'GET'} ${pathname}`);
    if (pathname === '/api/jobs' && init?.method === 'POST') return Response.json({ job_id: jobId, status: 'queued' }, { status: 202 });
    if (pathname.endsWith('/events')) return Response.json({ status: 'succeeded', done: true, next: 0, events: [] });
    if (pathname === `/api/jobs/${jobId}`) return Response.json({ status: 'succeeded', files: { official: 'yes', overlay: 'yes' } });
    if (pathname.endsWith('/result/official')) return Response.json(baseline);
    if (pathname.endsWith('/result/overlay')) return Response.json({ valid: true });
    if (pathname.endsWith('/result/baseline')) return Response.json(baseline);
    if (pathname.endsWith('/validate') && init?.method === 'POST') return Response.json({
      valid: true, metrics: { L: 42, X: 0, O: 0, B: 4, J: 44, area_gap_m2: 0 }, checks: [], violations: [],
    });
    return Response.json({ error: { message: 'Not found' } }, { status: 404 });
  };

  try {
    const result = await module.exports.solveCloudPlan({}, null, new AbortController().signal, () => {});
    assert.deepEqual(result.baseline, baseline);
    assert.deepEqual(result.baselineMetrics, { L: 42, X: 0, O: 0, B: 4, J: 44 });
    assert.ok(requests.includes(`POST /api/jobs/${jobId}/validate`));
  } finally {
    global.fetch = originalFetch;
    delete global.__cloudTestEnv;
  }
});
