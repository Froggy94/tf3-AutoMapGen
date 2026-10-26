"""Exercise the page's selection logic with Node.js (no browser or packages needed)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for GUI regression tests")

HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const page = fs.readFileSync(process.argv[2], 'utf8');
const code = page.slice(page.indexOf('<script>') + 8, page.lastIndexOf('</script>'));
new vm.Script(code); // syntax-check the complete page
function part(start, end) {
  assert.ok(code.includes(start) && code.includes(end));
  return code.slice(code.indexOf(start), code.indexOf(end));
}
const extracted = part('const rotation =', '// ---------- crop statistics') +
  part('function startBlockers()', '// ---------- server ----------') +
  part('function documentOfPage()', '// a project document onto the page') +
  part("rect.on('mousedown'", "map.on('dblclick'");
const prelude = `
  const fmt = (v, d = 2) => (+v).toFixed(d), esc = t => String(t);
  const elements = {}, $ = id => elements[id];
  for (const id of ['rotation','rotationPreset','rotationSlider','size','snap','scale','scaleTxt','mapInfo',
                    'townsMax','townsDist','townsInfo','areaWarn',
                    'coords','downloadBox','fileBox','tifInfo','climStep','realUntil','realInfo','minh','minInfo',
                    'demPath','demType','look','mapName','sink','graph','start','startInfo','save','stop','stopDel'])
    elements[id] = {value: '', textContent: '', innerHTML: '', checked: false, disabled: false, placeholder: '',
                    style: {}};
  elements.rotation.value = '45'; elements.mapName.value = 'Test'; elements.sink.value = '40';
  elements.demPath.value = 'test.tif'; elements.demType.value = 'GEDTM30';
  elements.look.value = '../presets/alpine_realistic.toml';
  elements.srcDownload = {checked: false}; elements.stop.disabled = true;
  const gameKm = () => [1, 5], scaleValue = () => 2, statsLater = () => {};
  let center = {lat: 47.3, lng: 11.4}, fixedCrop = {west: 11, east: 12, south: 47, north: 48};
  let doc = {graph: 'map', params: {crop: fixedCrop, rotation_deg: 0, max_towns: 42, other: 123}};
  let metricSelection = false, footprint = null, footprintKey = null, footprintPending = false,
      footprintError = null, footprintTask = null, footprintRequest = 0,
      shownFootprint = null, pendingFootprint = false;
  const SETUP = {api_key_set: true, scale_steps: [1, 2], sizes: [], formats: [], tile_m: 200, tile_px: 20,
                 towns_per_100_km2: 1, large_km2: 1e6, town_distance_game_m: 100, climate_reference_step_m: 10,
                 real_water_above_m: 20}, graphProblems = [], chosenSteps = () => ['map'];
  let tifBounds = [10, 46, 13, 49], cropMin = null, cropMax = null;
  const polygons = [], arrows = [], styles = [], mapEvents = {}, rectEvents = {};
  const L = {latLng: (lat, lng) => ({lat, lng}), DomEvent: {stop: () => {}}};
  const document = {addEventListener: () => {}};
  const map = {on: (event, handler) => {mapEvents[event] = handler;},
               dragging: {disable: () => {}, enable: () => {}}};
  const rect = {setLatLngs: r => polygons.push(r), setStyle: s => styles.push(s),
                on: (event, handler) => {rectEvents[event] = handler;}};
  const upArrow = {setLatLngs: a => arrows.push(a)}, upLabel = {setLatLng: () => {}};
  const tifRect = {remove: () => {}, setBounds: () => ({addTo: () => {}})};
  const pending = [];
  function api(url, body) {
    assert.equal(url, '/api/footprint');
    return new Promise((resolve, reject) => pending.push({resolve, reject, body}));
  }
  // Deliberately non-axis-aligned: a rectangular fixture cannot detect envelope fallback.
  const exact = {bounds: {west: 11, east: 12, south: 47, north: 48},
                 ring: [[11.2, 48], [12, 47.8], [11.8, 47], [11, 47.2]], top: [11.6, 47.9]};
  const latLngRing = f => f.ring.map(([lon, lat]) => [lat, lon]);
`;
const checks = CHECKS;
(async () => { await vm.runInNewContext(prelude + extracted + checks, {assert}); })().catch(e => {
  console.error(e); process.exitCode = 1;
});
"""


def _run_page_script(checks: str) -> None:
    page = Path(__file__).resolve().parents[2] / "src/automapgen/app/gui/page.html"
    # JSON encoding embeds the checks as a JS string, without interpolation or temporary files.
    script = HARNESS.replace("CHECKS", json.dumps(checks))
    result = subprocess.run(
        ["node", "-", str(page)], input=script, text=True, capture_output=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_gui_dragged_selection_keeps_the_rotated_polygon() -> None:
    _run_page_script(r"""
(async () => {
  metricSelection = true;
  const first = requestFootprint();
  pending[0].resolve(exact); await first;
  assert.deepEqual(polygons.at(-1), latLngRing(exact));
  assert.deepEqual(arrows.at(-1)[1], [47.9, 11.6]);
  assert.equal(startBlockers().length, 0);

  dragStart = {mouse: center, center};
  update(false); // pressing without moving must also block generation
  assert.equal(footprintPending, true);
  for (let i = 1; i <= 10; ++i) {
    center = {lat: 47.3 + i / 100, lng: 11.4 + i / 50};
    update(false);
  }
  assert.equal(pending.length, 1);
  assert.deepEqual(polygons.at(-1), [[48.1, 11.4], [47.9, 12.2], [47.1, 12], [47.3, 11.2]]);
  assert.equal($('coords').textContent, 'Computing footprint… (shown: last rotated selection)');
  assert.ok(startBlockers().some(s => s.includes('release the selection')));
  assert.equal(crop(), exact.bounds); // display copy never becomes authoritative
  assert.equal(styles.at(-1).color, '#e33');
  assert.ok($('tifInfo').innerHTML.includes('unknown'));

  dragStart = null; update();
  const after = footprintTask;
  update(); assert.equal(pending.length, 2);
  const exactAfter = {...exact, ring: [[11.3, 48.1], [12.1, 47.9], [11.9, 47.1], [11.1, 47.3]],
    top: [11.7, 48], bounds: {west: 11.1, east: 12.1, south: 47.1, north: 48.1}};
  pending[1].resolve(exactAfter); await after;
  assert.deepEqual(polygons.at(-1), latLngRing(exactAfter));
  assert.deepEqual(arrows.at(-1)[1], [48, 11.7]);
  assert.ok($('coords').textContent.startsWith('Source envelope'));
  assert.equal(crop(), exactAfter.bounds);
  assert.equal(startBlockers().length, 0);

  // A click/release without movement must not leave the pending flag stuck.
  dragStart = {mouse: center, center}; update(false);
  dragStart = null; update();
  assert.equal(pending.length, 2);
  assert.equal(footprintPending, false);
  assert.equal(startBlockers().length, 0);
})()
""")


def test_gui_response_during_drag_uses_request_center() -> None:
    _run_page_script(r"""
(async () => {
  metricSelection = true;
  const first = requestFootprint();
  dragStart = {mouse: center, center};
  center = {lat: 47.4, lng: 11.6}; update(false);
  pending[0].resolve(exact); await first;
  assert.deepEqual(footprint.center, [47.3, 11.4]);
  assert.deepEqual(polygons.at(-1), [[48.1, 11.4], [47.9, 12.2], [47.1, 12], [47.3, 11.2]]);
  assert.equal(pending.length, 1);
  assert.equal(footprintPending, true);
  assert.ok(startBlockers().length > 0);
  dragStart = null; update();
  assert.equal(pending.length, 2);
  const after = footprintTask;
  pending[1].resolve(exact); await after;
  assert.equal(footprintPending, false);
  assert.equal(startBlockers().length, 0);
})()
""")


def test_gui_dragging_legacy_crop_changes_location() -> None:
    _run_page_script(r"""
(async () => {
  elements.rotation.value = '90';
  const original = {...fixedCrop};
  update(false);
  rectEvents.mousedown({latlng: {...center}});
  mapEvents.mousemove({latlng: {lat: center.lat + 0.1, lng: center.lng + 0.2}});
  mapEvents.mouseup();
  assert.equal(fixedCrop, null);
  assert.equal(pending.length, 0); // cardinal legacy path still needs no metric request
  assert.equal(footprintPending, false);
  assert.notDeepEqual(crop(), original);
  assert.deepEqual(documentOfPage().params.crop, crop());
  assert.ok(!('center' in documentOfPage().params));
})()
""")


def test_gui_rotation_slider_and_presets() -> None:
    _run_page_script(r"""
(async () => {
  // Loading arbitrary angles preserves full precision, independently of the range input's whole-degree step.
  elements.rotation.value = '28.123456';
  syncRotationControls();
  assert.equal(elements.rotationPreset.value, 'custom');
  assert.equal(rotation(), 28.123456);
  assert.equal(documentOfPage().params.rotation_deg, 28.123456);
  for (const angle of ['0', '90', '180', '270']) {
    elements.rotation.value = angle; syncRotationControls();
    assert.equal(elements.rotationPreset.value, angle);
    assert.equal(elements.rotationSlider.value, angle);
  }

  elements.rotationSlider.value = '45'; rotationSliderChanged();
  assert.equal(rotation(), 45);
  assert.equal(elements.rotationPreset.value, 'custom');
  assert.equal(metricSelection, true);
  assert.equal(fixedCrop, null);
  assert.equal(pending.at(-1).body.selection.rotation_deg, 45);
  assert.equal(documentOfPage().params.rotation_deg, 45);

  // Slider use stays Custom even when it lands exactly on a cardinal bearing.
  elements.rotationSlider.value = '90'; rotationSliderChanged();
  assert.equal(elements.rotationPreset.value, 'custom');
  assert.equal(rotation(), 90);
  for (const angle of ['0', '90', '180', '270']) {
    elements.rotationPreset.value = angle; rotationPresetChanged();
    assert.equal(rotation(), +angle);
    assert.equal(elements.rotationSlider.value, angle);
    assert.equal(elements.rotationPreset.value, angle);
  }
  const requests = pending.length;
  elements.rotationPreset.value = 'custom'; rotationPresetChanged();
  assert.equal(rotation(), 270); assert.equal(pending.length, requests);
  elements.rotation.value = '359.99999'; rotationNumberChanged();
  assert.equal(elements.rotationPreset.value, 'custom');
  assert.equal(rotation(), 359.99999);
  assert.equal(documentOfPage().params.rotation_deg, 359.99999);
  elements.rotation.value = '180'; rotationNumberChanged();
  assert.equal(elements.rotationPreset.value, '180');
  // Selecting a cardinal preset does not silently convert an existing metric project to legacy geometry.
  assert.ok('center' in documentOfPage().params);
})()
""")


def test_gui_rotation_state_and_request_races() -> None:
    _run_page_script(r"""
(async () => {
  let p = documentOfPage().params;
  assert.deepEqual(p.center, {lon: 11.4, lat: 47.3});
  assert.equal(p.rotation_deg, 45); assert.equal(p.other, 123); assert.ok(!('crop' in p));
  elements.rotation.value = '90';
  p = documentOfPage().params;
  assert.deepEqual(p.crop, fixedCrop); assert.ok(!('center' in p));
  metricSelection = true;
  p = documentOfPage().params;
  assert.ok('center' in p); assert.ok(!('crop' in p));

  elements.rotation.value = '45';
  const first = requestFootprint();
  assert.equal(requestFootprint(), first); assert.equal(pending.length, 1);
  assert.ok(startBlockers().some(s => s.includes('computing')));
  center = {lat: 47.3, lng: 11.5};
  const second = requestFootprint();
  pending[0].resolve({bounds: {west: 0}}); await first;
  assert.equal(footprint, null); assert.equal(footprintPending, true);
  pending[1].resolve(exact); await second;
  assert.deepEqual(footprint.center, [47.3, 11.5]);
  assert.equal(polygons.length, 1); assert.equal(startBlockers().length, 0);
  assert.deepEqual(documentOfPage().params.center, {lon: 11.5, lat: 47.3});
  assert.equal(crop(), exact.bounds);

  center = {lat: 47.3, lng: 11.6};
  const failed = requestFootprint(); pending[2].reject('Missing coverage'); await failed;
  assert.ok(startBlockers().some(s => s.includes('Missing coverage')));
  center = {lat: 47.3, lng: 11.7};
  const retry = requestFootprint(); pending[3].resolve(exact); await retry;
  assert.equal(footprintError, null); assert.equal(startBlockers().length, 0);
  for (const value of ['', '-1', '360']) {
    elements.rotation.value = value;
    assert.ok(startBlockers().some(s => s.includes('bearing')));
  }

  // Switching back to legacy mode invalidates an outstanding metric response.
  elements.rotation.value = '28';
  center = {lat: 47.3, lng: 11.8};
  const stale = requestFootprint();
  metricSelection = false; elements.rotation.value = '0'; update(false);
  pending[4].resolve(exact); await stale;
  assert.equal(footprint, null); assert.equal(shownFootprint, null);
  assert.equal(footprintPending, false); assert.equal(pendingFootprint, false);
})()
""")
