const assert = require('node:assert/strict');
const {test} = require('node:test');
const {SourceAnalysisBinding} = require('../design/analysis-state.js');
const source = '公司回复：项目仍在审批，投产时间不确定。';
const analysis = {analysis_id: 'a'.repeat(32), source, facts: []};

test('an accepted analysis stays bound only to the exact source', () => {
  const binding = new SourceAnalysisBinding();
  assert.equal(binding.finish(binding.begin(source), analysis), true);
  assert.equal(binding.analysisId, analysis.analysis_id);
  binding.setSource(source + ' ');
  assert.equal(binding.analysisId, null);
  assert.equal(binding.outdated, true);
});

test('editing and restoring text still invalidates an in-flight response', () => {
  const binding = new SourceAnalysisBinding();
  const request = binding.begin(source);
  binding.setSource(source + '补充');
  binding.setSource(source);
  assert.equal(binding.finish(request, analysis), false);
  assert.equal(binding.analysisId, null);
});

test('an old response or error cannot replace a newer request', () => {
  const binding = new SourceAnalysisBinding();
  const first = binding.begin(source);
  const next = binding.begin(source);
  assert.equal(binding.finish(first, analysis), false);
  assert.equal(binding.fail(first, 'old error'), false);
  assert.equal(binding.pending, true);
  assert.equal(binding.finish(next, analysis), true);
});

test('switching to a new experiment discards pending analysis', () => {
  const binding = new SourceAnalysisBinding();
  const request = binding.begin(source);
  binding.reset(source);
  assert.equal(binding.finish(request, analysis), false);
  assert.equal(binding.analysisId, null);
});

test('mismatched responses and failures never create a binding', () => {
  const binding = new SourceAnalysisBinding();
  const request = binding.begin(source);
  assert.throws(() => binding.finish(request, {...analysis, source: source + '不同'}));
  binding.fail(request, 'failed');
  assert.equal(binding.analysisId, null);
  assert.equal(binding.pending, false);
  assert.equal(binding.error, 'failed');
});
