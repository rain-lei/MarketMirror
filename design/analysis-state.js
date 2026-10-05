/* Source revisions prevent a late model response from binding to edited text. */
(function (root) {
  'use strict';
  class SourceAnalysisBinding {
    constructor(source = '') {
      this.source = source;
      this.version = 0;
      this.analysis = null;
      this.pending = false;
      this.outdated = false;
      this.error = '';
    }
    setSource(source) {
      if (source === this.source) return false;
      const hadAnalysis = this.analysis !== null || this.pending || this.outdated;
      this.reset(source);
      this.outdated = hadAnalysis;
      return true;
    }
    reset(source = '') {
      this.source = source;
      this.version += 1;
      this.analysis = null;
      this.pending = false;
      this.outdated = false;
      this.error = '';
    }
    begin(source) {
      this.reset(source);
      this.pending = true;
      return {version: this.version, source};
    }
    isCurrent(ticket) {
      return this.pending && ticket.version === this.version && ticket.source === this.source;
    }
    finish(ticket, analysis) {
      if (!this.isCurrent(ticket)) return false;
      if (analysis.source !== this.source || !/^[a-f0-9]{32}$/.test(analysis.analysis_id)) {
        throw new Error('返回的分析与当前原文不一致');
      }
      this.analysis = analysis;
      this.pending = false;
      return true;
    }
    fail(ticket, message) {
      if (!this.isCurrent(ticket)) return false;
      this.pending = false;
      this.error = message;
      return true;
    }
    get analysisId() {
      return this.analysis?.analysis_id ?? null;
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {SourceAnalysisBinding};
  else root.SourceAnalysisBinding = SourceAnalysisBinding;
})(typeof window === 'undefined' ? globalThis : window);
