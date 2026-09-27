(function () {
  "use strict";
  const payload = JSON.parse(document.getElementById("review-data").textContent);
  const core = window.MarketMirrorReviewCore;
  const tasks = payload.tasks;
  let labels = JSON.parse(JSON.stringify(payload.labels));
  let current = 0;
  const byId = id => document.getElementById(id);

  function message(text, ok = false) {
    const target = byId("form-message");
    target.textContent = text;
    target.style.color = ok ? "#176b5b" : "#9d312d";
  }

  function countProgress() {
    const done = labels.filter(row => row.status === "labeled").length;
    const uncertain = labels.filter(row => row.status === "needs_review").length;
    byId("progress").textContent = `已完成 ${done}/${tasks.length} · 待复核 ${uncertain} · 未开始 ${tasks.length - done - uncertain}`;
  }

  function saveCurrentFields() {
    const row = labels[current];
    row.status = byId("status").value;
    row.notes = byId("notes").value;
    row.annotator_id = row.status === "unlabeled" ? null : byId("annotator-id").value.trim();
    countProgress();
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderEvents() {
    const list = byId("event-list");
    list.replaceChildren();
    const events = labels[current].events;
    if (!events.length) {
      list.appendChild(element("div", "empty", "尚无事件。确定没有明确事件时，直接将本条标为“已完成”。"));
      return;
    }
    events.forEach((event, index) => {
      const card = element("div", "event-card");
      card.appendChild(element("b", "", `${index + 1}. ${event.event_type} · ${event.direction} · ${event.horizon}`));
      card.appendChild(element("small", "", `强度 ${event.intensity} · 不确定性 ${event.uncertainty} · 行业 ${event.affected_industries.join("、") || "无"}`));
      event.evidence_spans.forEach(span => {
        card.appendChild(element("blockquote", "", `${span.source} [${span.start}, ${span.end})：${span.quote}`));
      });
      const addQuote = element("button", "", "将下方引用添加到此事件");
      addQuote.type = "button";
      addQuote.addEventListener("click", () => {
        try {
          const span = core.exactSpan(tasks[current].segments, byId("quote-source").value, byId("quote").value);
          if (event.evidence_spans.some(existing => existing.source === span.source
              && existing.start === span.start && existing.end === span.end)) {
            throw new Error("此事件已有相同证据");
          }
          event.evidence_spans.push(span);
          byId("status").value = "labeled";
          saveCurrentFields();
          renderEvents();
          message("已添加逐字引用", true);
        } catch (error) { message(error.message); }
      });
      card.appendChild(addQuote);
      const remove = element("button", "", "移除该事件");
      remove.type = "button";
      remove.addEventListener("click", () => {
        events.splice(index, 1);
        renderEvents();
        message("已移除事件；若无明确事件仍可标为已完成", true);
      });
      card.appendChild(remove);
      list.appendChild(card);
    });
  }

  function render() {
    const task = tasks[current];
    const row = labels[current];
    byId("position").textContent = `第 ${current + 1} / ${tasks.length} 条`;
    byId("stage").textContent = task.stage === "reply" ? "回复阶段：提问 + 已可见回复" : "提问阶段：仅提问";
    const segments = byId("segments");
    segments.replaceChildren();
    const sourceSelect = byId("quote-source");
    sourceSelect.replaceChildren();
    task.segments.forEach(segment => {
      const card = element("section", "segment");
      card.appendChild(element("h2", "", segment.source === "reply" ? "公司回复" : "用户提问"));
      card.appendChild(element("pre", "", segment.text));
      segments.appendChild(card);
      const option = element("option", "", segment.source === "reply" ? "回复" : "提问");
      option.value = segment.source;
      sourceSelect.appendChild(option);
    });
    if (task.stage === "reply") sourceSelect.value = "reply";
    byId("status").value = row.status;
    byId("notes").value = row.notes;
    byId("quote").value = "";
    renderEvents();
    countProgress();
    message("");
  }

  function navigate(target) {
    saveCurrentFields();
    current = Math.max(0, Math.min(tasks.length - 1, target));
    render();
    window.scrollTo({top: 0, behavior: "instant"});
  }

  byId("slot-name").textContent = payload.slot === "reviewer_a" ? "审核者 A 专用包" : "审核者 B 专用包";
  byId("previous").addEventListener("click", () => navigate(current - 1));
  byId("next").addEventListener("click", () => navigate(current + 1));
  byId("next-open").addEventListener("click", () => {
    saveCurrentFields();
    for (let step = 1; step <= tasks.length; step++) {
      const candidate = (current + step) % tasks.length;
      if (labels[candidate].status !== "labeled") { current = candidate; break; }
    }
    render();
    window.scrollTo({top: 0, behavior: "instant"});
  });
  byId("status").addEventListener("change", saveCurrentFields);
  byId("notes").addEventListener("change", saveCurrentFields);
  byId("annotator-id").addEventListener("change", saveCurrentFields);

  byId("add-event").addEventListener("click", () => {
    try {
      const event = core.makeEvent({
        event_type: byId("event-type").value, direction: byId("direction").value,
        horizon: byId("horizon").value, intensity: byId("intensity").value,
        uncertainty: byId("uncertainty").value, industries: byId("industries").value,
        source: byId("quote-source").value, quote: byId("quote").value
      }, tasks[current].segments);
      labels[current].events.push(event);
      byId("status").value = "labeled";
      saveCurrentFields();
      renderEvents();
      byId("quote").value = "";
      message("事件已添加。可继续添加事件，或将其他引用加到已有事件。", true);
    } catch (error) { message(error.message); }
  });

  byId("download").addEventListener("click", () => {
    try {
      saveCurrentFields();
      const identity = byId("annotator-id").value.trim();
      labels.forEach(row => { row.annotator_id = row.status === "unlabeled" ? null : identity; });
      const audit = core.validateDraft(tasks, labels);
      const content = labels.map(row => JSON.stringify(row)).join("\n") + "\n";
      const url = URL.createObjectURL(new Blob([content], {type: "application/x-ndjson;charset=utf-8"}));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${payload.slot}_labels_${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      message(`已下载：完成 ${audit.completed} 条，待复核 ${audit.needsReview} 条。请保存文件。`, true);
    } catch (error) { message(error.message); }
  });

  byId("import-file").addEventListener("change", async event => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      const content = await file.text();
      const rows = content.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
      const audit = core.validateDraft(tasks, rows);
      labels = rows;
      byId("annotator-id").value = audit.annotatorId || "";
      current = 0;
      render();
      message(`已导入当前审核包草稿：完成 ${audit.completed} 条。`, true);
    } catch (error) { message(`导入失败：${error.message}`); }
    event.target.value = "";
  });

  render();
})();
