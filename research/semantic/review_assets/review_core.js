/* Source-bound helpers shared by the offline reviewer page and Node tests. */
(function (root) {
  "use strict";
  const EVENT_TYPES = ["regulation", "liquidity", "earnings", "governance", "other"];
  const DIRECTIONS = ["positive", "negative", "neutral", "unknown"];
  const HORIZONS = ["short", "medium", "long", "unknown"];

  function exactSpan(segments, source, quote) {
    if (typeof source !== "string" || typeof quote !== "string" || !quote) {
      throw new Error("证据来源和引用不能为空");
    }
    const segment = segments.find(part => part.source === source);
    if (!segment) throw new Error("证据来源在此阶段不可见");
    const location = segment.text.indexOf(quote);
    if (location < 0) throw new Error("引用未逐字出现在指定原文中");
    if (segment.text.indexOf(quote, location + 1) >= 0) {
      throw new Error("引用重复出现，请增加上下文使其唯一");
    }
    const start = Array.from(segment.text.slice(0, location)).length;
    return {source, start, end: start + Array.from(quote).length, quote};
  }

  function parseIndustries(text) {
    const values = text.split(/[，,]/).map(value => value.trim()).filter(Boolean);
    if (values.length !== new Set(values).size) throw new Error("受影响行业不能重复");
    return values;
  }

  function makeEvent(form, segments) {
    if (!EVENT_TYPES.includes(form.event_type) || !DIRECTIONS.includes(form.direction)
        || !HORIZONS.includes(form.horizon)) throw new Error("事件类别、方向或时间范围无效");
    const intensity = Number(form.intensity);
    const uncertainty = Number(form.uncertainty);
    if (form.intensity === "" || form.uncertainty === "" || !Number.isFinite(intensity)
        || !Number.isFinite(uncertainty) || intensity < 0 || intensity > 1
        || uncertainty < 0 || uncertainty > 1) throw new Error("强度和不确定性必须是 0～1 的数字");
    return {event_type: form.event_type, direction: form.direction,
      affected_industries: parseIndustries(form.industries || ""), horizon: form.horizon,
      intensity, uncertainty,
      evidence_spans: [exactSpan(segments, form.source, form.quote)]};
  }

  function validateDraft(tasks, rows) {
    if (!Array.isArray(rows) || rows.length !== tasks.length) throw new Error("草稿条数与任务包不同");
    const fields = ["item_id", "source_text_sha256", "annotator_id", "status", "events", "notes"];
    let annotatorId = null;
    rows.forEach((row, index) => {
      const task = tasks[index];
      if (!row || typeof row !== "object" || Array.isArray(row)
          || Object.keys(row).sort().join("|") !== fields.slice().sort().join("|")) {
        throw new Error(`第 ${index + 1} 条标签字段不符`);
      }
      if (row.item_id !== task.item_id || row.source_text_sha256 !== task.source_text_sha256) {
        throw new Error(`第 ${index + 1} 条与当前审核任务不匹配`);
      }
      if (!["unlabeled", "labeled", "needs_review"].includes(row.status)
          || !Array.isArray(row.events) || typeof row.notes !== "string") {
        throw new Error(`第 ${index + 1} 条状态或事件格式无效`);
      }
      if (row.status === "unlabeled") {
        if (row.annotator_id !== null || row.events.length) throw new Error(`第 ${index + 1} 条未审核但已有标签`);
      } else {
        if (typeof row.annotator_id !== "string" || !row.annotator_id.trim()) {
          throw new Error(`第 ${index + 1} 条缺少审核者 ID`);
        }
        if (annotatorId !== null && annotatorId !== row.annotator_id) throw new Error("草稿含多个审核者 ID");
        annotatorId = row.annotator_id;
      }
      if (row.status === "needs_review" && !row.notes.trim()) {
        throw new Error(`第 ${index + 1} 条待复核需要说明原因`);
      }
      row.events.forEach((event, eventIndex) => {
        const expected = ["event_type", "direction", "affected_industries", "horizon",
          "intensity", "uncertainty", "evidence_spans"];
        if (!event || typeof event !== "object" || Array.isArray(event)
            || Object.keys(event).sort().join("|") !== expected.slice().sort().join("|")) {
          throw new Error(`第 ${index + 1} 条事件 ${eventIndex + 1} 字段无效`);
        }
        if (!EVENT_TYPES.includes(event.event_type) || !DIRECTIONS.includes(event.direction)
            || !HORIZONS.includes(event.horizon)
            || !Array.isArray(event.affected_industries)
            || event.affected_industries.some(value => typeof value !== "string" || !value.trim())
            || new Set(event.affected_industries).size !== event.affected_industries.length
            || typeof event.intensity !== "number" || !Number.isFinite(event.intensity)
            || event.intensity < 0 || event.intensity > 1
            || typeof event.uncertainty !== "number" || !Number.isFinite(event.uncertainty)
            || event.uncertainty < 0 || event.uncertainty > 1
            || !Array.isArray(event.evidence_spans) || !event.evidence_spans.length) {
          throw new Error(`第 ${index + 1} 条事件 ${eventIndex + 1} 取值无效`);
        }
        event.evidence_spans.forEach(span => {
          if (!span || typeof span !== "object" || Array.isArray(span)
              || Object.keys(span).sort().join("|") !== ["source", "start", "end", "quote"].sort().join("|")) {
            throw new Error(`第 ${index + 1} 条证据字段无效`);
          }
          const calculated = exactSpan(task.segments, span.source, span.quote);
          if (span.start !== calculated.start || span.end !== calculated.end) {
            throw new Error(`第 ${index + 1} 条证据位置与原文不符`);
          }
        });
      });
    });
    return {annotatorId,
      completed: rows.filter(row => row.status === "labeled").length,
      needsReview: rows.filter(row => row.status === "needs_review").length};
  }

  const api = {exactSpan, parseIndustries, makeEvent, validateDraft};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.MarketMirrorReviewCore = api;
})(typeof window !== "undefined" ? window : globalThis);
