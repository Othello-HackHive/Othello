const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

function loadTypeScript(name, resolve) {
  const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'lib', `${name}.ts`), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', compiled)(resolve, module, module.exports);
  return module.exports;
}

const resplan = loadTypeScript('resplan', () => { throw new Error('Unexpected import'); });
const { optimize } = loadTypeScript('optimizer', (name) => {
  assert.equal(name, './resplan');
  return resplan;
});
const { createShareLink, readShareLink } = loadTypeScript('shareLink', () => {
  throw new Error('Unexpected runtime import');
});

for (const presetId of resplan.PRESET_IDS) {
  test(`moving a hub reroutes its ring in ${presetId}`, () => {
    const { plan } = resplan.normalizePlan(resplan.PRESET_JSON[presetId], presetId);
    const base = optimize(plan);
    const hub = base.hubs[0];
    const position = [hub.location[0] + 0.15, hub.location[1] + 0.15];
    const edited = optimize(plan, undefined, { base, hubId: hub.hub_id, position });

    assert.ok(Math.hypot(edited.hubs[0].location[0] - position[0], edited.hubs[0].location[1] - position[1]) < 0.001);
    assert.deepEqual(edited.hubs[0].closed_sequence, hub.closed_sequence);
    assert.notDeepEqual(edited.hubs[0].route_segments, hub.route_segments);
    assert.ok(Number.isFinite(edited.metrics.J));
    assert.equal(edited.output.reported_metrics.cost_J, edited.metrics.J);
    base.hubs.slice(1).forEach((other, index) => {
      assert.deepEqual(edited.hubs[index + 1].route_segments, other.route_segments);
    });
  });
}

test('a share link restores the edited result and view settings', async () => {
  global.window = { location: { origin: 'https://example.test', pathname: '/viewer', search: '' } };
  const { plan } = resplan.normalizePlan(resplan.PRESET_JSON.BENCHMARK_6R);
  const base = optimize(plan);
  const hub = base.hubs[0];
  const draft = optimize(plan, undefined, {
    base, hubId: hub.hub_id, position: [hub.location[0] + 0.15, hub.location[1] + 0.15],
  });
  const shared = {
    v: 1, key: 'BENCHMARK_6R', label: '6-room example', plan: resplan.PRESET_JSON.BENCHMARK_6R,
    official: null, overlay: null, baseline: null, events: [], draft, draftStatus: 'preview',
    view: { zones: true, routes: true, heatmap: true, wallSlice: 1.2, zoom: 1.3 },
  };
  const url = await createShareLink(shared);
  assert.ok(url.startsWith('https://example.test/viewer#share='));
  assert.deepEqual(await readShareLink(new URL(url).hash), shared);
});
