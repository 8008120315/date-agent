const REVIEW_DRAFT_KEY = "review_draft";

const reviewState = {
  latestRequestSeq: 0,
  autoContext: {
    done: [],
    undone: [],
    blockers: [],
    dates: [],
  },
  lastReviewResponse: null,
  lastRenderedMarkdown: "",
  draftContent: "",
  isEditing: false,
  draftSaveTimer: null,
  filterKey: "",
};

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function readErrorMessage(response) {
  const fallback = `HTTP ${response.status}`;
  try {
    const text = await response.text();
    if (!text) return fallback;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object" && typeof parsed.detail === "string" && parsed.detail.trim()) {
        return parsed.detail.trim();
      }
    } catch {
      // ignore
    }
    return text;
  } catch {
    return fallback;
  }
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function putJson(url, payload) {
  const response = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

function toDateKey(dateObj) {
  const y = dateObj.getFullYear();
  const m = String(dateObj.getMonth() + 1).padStart(2, "0");
  const d = String(dateObj.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function parseDateKey(dateKey) {
  const [year, month, day] = String(dateKey || "").split("-").map(Number);
  return new Date(year, month - 1, day);
}

function addDays(dateObj, delta) {
  const d = new Date(dateObj.getFullYear(), dateObj.getMonth(), dateObj.getDate());
  d.setDate(d.getDate() + delta);
  return d;
}

function todayDateKey() {
  return toDateKey(new Date());
}

function persistDraftToLocal(reason = "") {
  try {
    const payload = {
      content: reviewState.draftContent || "",
      review_response: reviewState.lastReviewResponse,
      filter_key: reviewState.filterKey || "",
      updated_at: new Date().toISOString(),
      reason,
    };
    localStorage.setItem(REVIEW_DRAFT_KEY, JSON.stringify(payload));
  } catch {
    // ignore localStorage failures
  }
}

function clearDraftCache() {
  try {
    localStorage.removeItem(REVIEW_DRAFT_KEY);
  } catch {
    // ignore
  }
}

function scheduleDraftPersist() {
  if (reviewState.draftSaveTimer) {
    clearTimeout(reviewState.draftSaveTimer);
  }
  reviewState.draftSaveTimer = setTimeout(() => {
    persistDraftToLocal("editing");
  }, 420);
}

function restoreDraftFromLocal() {
  try {
    const raw = localStorage.getItem(REVIEW_DRAFT_KEY);
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && typeof parsed.content === "string") {
        return {
          content: parsed.content,
          response: parsed.review_response || null,
          filterKey: String(parsed.filter_key || "").trim(),
        };
      }
    } catch {
      // backward compatibility: plain markdown string
      if (typeof raw === "string" && raw.trim()) {
        return { content: raw, response: null };
      }
    }
    return null;
  } catch {
    return null;
  }
}

function buildReviewFilterKey(form) {
  return JSON.stringify({
    range_type: String(form?.range_type?.value || "day"),
    date: String(form?.date?.value || "").trim(),
    start_date: String(form?.start_date?.value || "").trim(),
    end_date: String(form?.end_date?.value || "").trim(),
  });
}

function normalizeObjectiveRow(item, fallbackStatus) {
  const statusRaw = String(item?.status || fallbackStatus || "todo").toLowerCase();
  const status = statusRaw === "done" || statusRaw === "partial" || statusRaw === "todo" ? statusRaw : fallbackStatus;
  const percentRaw = Number(item?.percent || 0);
  const basePercent = Number.isFinite(percentRaw) ? Math.max(0, Math.min(100, Math.round(percentRaw))) : 0;
  const percent = status === "done" ? 100 : status === "todo" ? 0 : Math.max(1, Math.min(99, basePercent || 50));
  return {
    date: String(item?.date || "").trim(),
    title: String(item?.title || "").trim() || "未命名任务",
    priority: String(item?.priority || "P2").trim().toUpperCase() || "P2",
    status,
    percent,
    note: String(item?.note || "").trim(),
  };
}

function buildFrontendObjectivePayload(autoContext) {
  return {
    frontend_completed_list: (autoContext?.done || []).map((row) => normalizeObjectiveRow(row, "done")),
    frontend_incomplete_list: (autoContext?.undone || []).map((row) => normalizeObjectiveRow(row, "todo")),
    frontend_blocked_list: (autoContext?.blockers || []).map((row) => normalizeObjectiveRow(row, "todo")),
    frontend_dates: Array.isArray(autoContext?.dates) ? autoContext.dates : [],
  };
}

function resolvePeriod(rangeType, anchorDate, startDate, endDate) {
  const anchor = parseDateKey(anchorDate || todayDateKey());
  if (rangeType === "day") {
    return { start: toDateKey(anchor), end: toDateKey(anchor), label: "某一天" };
  }
  if (rangeType === "last_3_days") {
    return { start: toDateKey(addDays(anchor, -2)), end: toDateKey(anchor), label: "最近三天" };
  }
  if (rangeType === "last_week") {
    const weekday = anchor.getDay() === 0 ? 7 : anchor.getDay();
    const thisWeekMonday = addDays(anchor, -(weekday - 1));
    const lastWeekMonday = addDays(thisWeekMonday, -7);
    const lastWeekSunday = addDays(thisWeekMonday, -1);
    return {
      start: toDateKey(lastWeekMonday),
      end: toDateKey(lastWeekSunday),
      label: "上周",
    };
  }
  return {
    start: String(startDate || "").trim(),
    end: String(endDate || "").trim(),
    label: "自定义",
  };
}

function enumerateDateKeys(startDate, endDate) {
  const start = parseDateKey(startDate);
  const end = parseDateKey(endDate);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) {
    return [];
  }
  const days = [];
  let cursor = new Date(start.getFullYear(), start.getMonth(), start.getDate());
  while (cursor <= end && days.length < 31) {
    days.push(toDateKey(cursor));
    cursor = addDays(cursor, 1);
  }
  return days;
}

function normalizeTaskStatus(status) {
  const value = String(status || "").toLowerCase();
  if (value === "done" || value === "partial" || value === "todo") return value;
  return "todo";
}

function priorityTagClass(priority) {
  const value = String(priority || "").toUpperCase();
  if (value === "P0") return "is-p0";
  if (value === "P1") return "is-p1";
  return "is-p2";
}

function renderProgressTag(status, percent) {
  if (status === "done") {
    return `<span class="review-state-tag is-done">已完成</span>`;
  }
  if (status === "todo") {
    return `<span class="review-state-tag is-todo">未完成</span>`;
  }
  const safePercent = Math.max(0, Math.min(100, Number(percent || 0)));
  return `
    <span class="review-state-tag is-partial">
      <span class="progress-track"><span class="progress-fill" style="width:${safePercent}%"></span></span>
      <span class="progress-text">部分完成 ${safePercent}%</span>
    </span>
  `;
}

function renderAutoList(targetEl, items, emptyText) {
  if (!targetEl) return;
  const panel = targetEl.closest(".review-context-panel");
  if (!items.length) {
    panel?.classList.add("is-empty-state");
    targetEl.innerHTML = `
      <li class="is-empty">
        <span class="empty-icon" aria-hidden="true">☑</span>
        <span>${escapeHtml(emptyText)}</span>
      </li>
    `;
    return;
  }
  panel?.classList.remove("is-empty-state");
  targetEl.innerHTML = items
    .map((item) => {
      const statusTag = renderProgressTag(item.status, item.percent);
      const note = item.note ? `<p class="auto-task-note">备注：${escapeHtml(item.note)}</p>` : "";
      return `
        <li class="auto-task-item">
          <div class="auto-task-main">
            <span class="auto-task-title">${escapeHtml(item.title)}</span>
            <div class="auto-task-tags">
              <span class="review-priority-tag ${priorityTagClass(item.priority)}">${escapeHtml(item.priority || "P2")}</span>
              ${statusTag}
            </div>
          </div>
          ${note}
        </li>
      `;
    })
    .join("");
}

function autoResizeTextarea(textarea) {
  if (!textarea) return;
  textarea.style.height = "auto";
  textarea.style.height = `${Math.max(textarea.scrollHeight, 78)}px`;
}

function parseInlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

function renderMarkdownToHtml(markdownText) {
  const lines = String(markdownText || "").split(/\r?\n/);
  const html = [];
  let listType = "";

  const closeList = () => {
    if (listType === "ul") html.push("</ul>");
    if (listType === "ol") html.push("</ol>");
    listType = "";
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      closeList();
      continue;
    }
    if (line.startsWith("### ")) {
      closeList();
      html.push(`<h4>${parseInlineMarkdown(line.slice(4))}</h4>`);
      continue;
    }
    if (line.startsWith("## ")) {
      closeList();
      html.push(`<h3>${parseInlineMarkdown(line.slice(3))}</h3>`);
      continue;
    }
    if (line.startsWith("# ")) {
      closeList();
      html.push(`<h2>${parseInlineMarkdown(line.slice(2))}</h2>`);
      continue;
    }

    const ul = line.match(/^- (.+)$/);
    if (ul) {
      if (listType !== "ul") {
        closeList();
        html.push("<ul>");
        listType = "ul";
      }
      html.push(`<li>${parseInlineMarkdown(ul[1])}</li>`);
      continue;
    }

    const ol = line.match(/^\d+\.\s+(.+)$/);
    if (ol) {
      if (listType !== "ol") {
        closeList();
        html.push("<ol>");
        listType = "ol";
      }
      html.push(`<li>${parseInlineMarkdown(ol[1])}</li>`);
      continue;
    }

    closeList();
    html.push(`<p>${parseInlineMarkdown(line)}</p>`);
  }

  closeList();
  return html.join("");
}

function setContextStatus(message, isError = false) {
  const el = $("review-context-status");
  if (!el) return;
  el.textContent = message;
  el.classList.toggle("is-error", isError);
}

async function loadAutoContext(form) {
  const rangeType = String(form.range_type?.value || "day");
  const anchorDate = String(form.date?.value || todayDateKey()).trim();
  const startDate = String(form.start_date?.value || "").trim();
  const endDate = String(form.end_date?.value || "").trim();
  const period = resolvePeriod(rangeType, anchorDate, startDate, endDate);

  if (!period.start || !period.end) {
    setContextStatus("请先选择有效的时间范围。", true);
    reviewState.autoContext = { done: [], undone: [], blockers: [], dates: [] };
    return null;
  }
  const dateKeys = enumerateDateKeys(period.start, period.end);
  if (!dateKeys.length) {
    setContextStatus("时间范围无效，请调整日期。", true);
    reviewState.autoContext = { done: [], undone: [], blockers: [], dates: [] };
    return null;
  }

  const seq = reviewState.latestRequestSeq + 1;
  reviewState.latestRequestSeq = seq;
  setContextStatus("正在拉取该时间段内任务数据...");

  try {
    const dayPlans = await Promise.all(
      dateKeys.map((dateKey) => getJson(`/api/plan/day?date=${encodeURIComponent(dateKey)}`))
    );
    if (seq !== reviewState.latestRequestSeq) return null;

    const done = [];
    const undone = [];
    const blockers = [];

    dayPlans.forEach((plan, idx) => {
      const date = dateKeys[idx];
      const items = Array.isArray(plan?.plan_items) ? plan.plan_items : [];
      items.forEach((item) => {
        const title = String(item?.title || "未命名任务").trim();
        const status = normalizeTaskStatus(item?.progress_status);
        const percent = Number(item?.progress_percent || 0);
        const note = String(item?.progress_note || "").trim();
        const priority = String(item?.priority || "P2").toUpperCase();

        if (status === "done") {
          done.push({ date, title, priority, status, percent, note });
        } else {
          undone.push({ date, title, priority, status, percent, note });
        }

        const looksBlocked = Boolean(note) && /(阻塞|卡住|等待|依赖|延期|风险|blocked|blocker)/i.test(note);
        if (looksBlocked) {
          blockers.push({ date, title, priority, status, percent, note });
        }
      });
    });

    reviewState.autoContext = { done, undone, blockers, dates: dateKeys };

    renderAutoList($("auto-done-list"), done, "系统未识别到已完成任务");
    renderAutoList($("auto-undone-list"), undone, "系统未识别到未完成任务");
    renderAutoList($("auto-blockers-list"), blockers, "系统未识别到阻塞任务");
    setContextStatus(`已关联 ${dateKeys.length} 天任务数据（已完成 ${done.length} / 未完成 ${undone.length} / 阻塞 ${blockers.length}）`);
    return reviewState.autoContext;
  } catch (err) {
    if (seq !== reviewState.latestRequestSeq) return null;
    setContextStatus(`拉取任务数据失败：${String(err)}`, true);
    reviewState.autoContext = { done: [], undone: [], blockers: [], dates: dateKeys };
    renderAutoList($("auto-done-list"), [], "拉取失败");
    renderAutoList($("auto-undone-list"), [], "拉取失败");
    renderAutoList($("auto-blockers-list"), [], "拉取失败");
    return null;
  }
}

function buildReviewMarkdown(data) {
  const periodLabel = String(data?.period_label || "").trim() || "复盘";
  const summary = String(data?.daily_summary || "").trim();
  const actions = Array.isArray(data?.tomorrow_actions) ? data.tomorrow_actions : [];
  const coveredDates = Array.isArray(data?.covered_dates) ? data.covered_dates : [];

  const lines = [];
  lines.push(`# ${periodLabel}`);
  lines.push("");
  lines.push("## 总体总结");
  lines.push(summary || "暂无内容");
  lines.push("");
  lines.push("## 下一步行动");
  if (!actions.length) {
    lines.push("- 暂无");
  } else {
    actions.forEach((item) => lines.push(`- ${String(item).trim()}`));
  }
  lines.push("");
  lines.push("## 覆盖日期");
  if (!coveredDates.length) {
    lines.push("- 暂无");
  } else {
    coveredDates.forEach((date) => lines.push(`- ${date}`));
  }
  return lines.join("\n");
}

function renderReviewResult(markdownText) {
  const container = $("review-result");
  if (!container) return;
  const text = String(markdownText || "").trim();
  if (!text) {
    container.classList.add("is-empty");
    container.innerHTML = `<p class="hint">生成后将在这里展示结构化复盘内容。</p>`;
    return;
  }
  container.classList.remove("is-empty");
  container.innerHTML = `<div class="markdown-body">${renderMarkdownToHtml(text)}</div>`;
}

function extractSummaryFromDraft(markdownText) {
  const lines = String(markdownText || "").split(/\r?\n/);
  let inSummary = false;
  let firstNonHeader = "";
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("## ")) {
      inSummary = line.includes("总体总结");
      continue;
    }
    if (line.startsWith("# ")) continue;
    if (inSummary) {
      return line.replace(/^[-*]\s*/, "").slice(0, 120);
    }
    if (!firstNonHeader) {
      firstNonHeader = line.replace(/^[-*]\s*/, "");
    }
  }
  return firstNonHeader.slice(0, 120);
}

function extractActionsFromDraft(markdownText) {
  const lines = String(markdownText || "").split(/\r?\n/);
  const actions = [];
  let inActions = false;
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("## ")) {
      if (line.includes("下一步行动")) {
        inActions = true;
        continue;
      }
      if (inActions) break;
    }
    if (!inActions) continue;
    const bullet = line.match(/^[-*]\s+(.+)$/);
    const ordered = line.match(/^\d+\.\s+(.+)$/);
    if (bullet) {
      actions.push(bullet[1].trim());
      continue;
    }
    if (ordered) {
      actions.push(ordered[1].trim());
    }
  }
  return actions.filter(Boolean);
}

async function saveReviewRecord(fallbackDate = todayDateKey()) {
  const content = String(reviewState.draftContent || "").trim();
  if (!content) return;
  const data = reviewState.lastReviewResponse;
  const summary = extractSummaryFromDraft(content) || String(data?.daily_summary || "").trim();
  const actions = extractActionsFromDraft(content);
  await postJson("/api/memory/write", {
    date: String(data?.date || fallbackDate || todayDateKey()),
    type: "review",
    content,
    summary: summary.slice(0, 120) || "复盘记录",
    metadata: {
      period_label: data?.period_label || "",
      covered_dates: data?.covered_dates || [],
      source_model: data?.source_model || "",
      memory_id: data?.memory_id || null,
      actions,
    },
  });
}

function getTomorrowActionsFromReview() {
  const fromDraft = extractActionsFromDraft(reviewState.draftContent);
  if (fromDraft.length) return fromDraft;
  const rows = Array.isArray(reviewState.lastReviewResponse?.tomorrow_actions)
    ? reviewState.lastReviewResponse.tomorrow_actions
    : [];
  return rows.map((item) => String(item || "").trim()).filter(Boolean);
}

function buildTomorrowPlanItems(actions) {
  return actions.map((action, index) => ({
    title: action,
    priority: index === 0 ? "P0" : index <= 2 ? "P1" : "P2",
    estimate_hours: 1,
    done_definition: "完成该行动并记录可验证结果。",
    checklist: [
      { content: action, is_done: false },
      { content: "执行后补充结果与备注", is_done: false },
    ],
    progress_status: "todo",
    progress_percent: 0,
    progress_note: "",
  }));
}

async function importActionsToTomorrowTasks(anchorDate = todayDateKey(), periodLabel = "今日") {
  const actions = getTomorrowActionsFromReview();
  if (!actions.length) {
    return { imported: false, reason: "no_actions" };
  }

  const tomorrow = toDateKey(addDays(parseDateKey(anchorDate), 1));
  const goalText = `承接复盘（${String(periodLabel || "今日")}）的下一步行动`;

  await postJson("/api/plan", {
    date: tomorrow,
    goal_text: goalText,
  });

  await putJson("/api/plan/day", {
    date: tomorrow,
    goal_text: goalText,
    plan_items: buildTomorrowPlanItems(actions),
  });

  return { imported: true, tomorrow, count: actions.length };
}

function bindReviewPage() {
  const form = $("review-form");
  if (!form) return;

  const customRangeFields = $("custom-range-fields");
  const submitBtn = $("review-submit-btn");
  const submitStatus = $("review-submit-status");
  const resultActions = $("review-result-actions");
  const copyBtn = $("copy-review-btn");
  const saveBtn = $("save-review-btn");
  const toggleEditBtn = $("toggle-review-edit-btn");
  const editorWrap = $("review-editor-wrap");
  const editor = $("review-draft-editor");
  const exitEditBtn = $("exit-review-edit-btn");
  const applyEditBtn = $("apply-review-edit-btn");
  const resultView = $("review-result");

  const today = todayDateKey();
  if (!form.date.value) {
    form.date.value = today;
  }
  reviewState.filterKey = buildReviewFilterKey(form);

  const updateSubmitButtonText = (isLoading = false) => {
    const hasResult = Boolean(String(reviewState.draftContent || "").trim());
    if (isLoading) {
      submitBtn.textContent = hasResult ? "重新生成中..." : "生成中...";
      return;
    }
    submitBtn.textContent = hasResult ? "重新生成" : "生成复盘总结";
  };

  const updateEditUI = () => {
    const hasDraft = Boolean(String(reviewState.draftContent || "").trim());
    toggleEditBtn?.classList.toggle("is-hidden", !hasDraft);
    resultActions?.classList.toggle("is-hidden", !hasDraft);

    if (!hasDraft) {
      reviewState.isEditing = false;
      resultView?.classList.remove("is-hidden");
      editorWrap?.classList.add("is-hidden");
      renderReviewResult("");
      copyBtn?.classList.add("is-hidden");
      saveBtn?.classList.add("is-hidden");
      exitEditBtn?.classList.add("is-hidden");
      applyEditBtn?.classList.add("is-hidden");
      toggleEditBtn && (toggleEditBtn.textContent = "编辑内容");
      return;
    }

    if (reviewState.isEditing) {
      resultView?.classList.add("is-hidden");
      editorWrap?.classList.remove("is-hidden");
      if (editor && editor.value !== reviewState.draftContent) {
        editor.value = reviewState.draftContent;
      }
      copyBtn?.classList.add("is-hidden");
      saveBtn?.classList.add("is-hidden");
      exitEditBtn?.classList.remove("is-hidden");
      applyEditBtn?.classList.remove("is-hidden");
      toggleEditBtn && (toggleEditBtn.textContent = "编辑中");
    } else {
      resultView?.classList.remove("is-hidden");
      editorWrap?.classList.add("is-hidden");
      copyBtn?.classList.remove("is-hidden");
      saveBtn?.classList.remove("is-hidden");
      exitEditBtn?.classList.add("is-hidden");
      applyEditBtn?.classList.add("is-hidden");
      toggleEditBtn && (toggleEditBtn.textContent = "编辑内容");
      renderReviewResult(reviewState.draftContent);
    }
  };

  const setDraftContent = (content, { persist = true, keepEditing = false } = {}) => {
    reviewState.draftContent = String(content || "");
    reviewState.lastRenderedMarkdown = reviewState.draftContent;
    if (!keepEditing) {
      reviewState.isEditing = false;
    }
    if (persist) {
      persistDraftToLocal("set_draft");
    }
    updateSubmitButtonText(false);
    updateEditUI();
  };

  const applyEditorValueToState = ({ persist = true } = {}) => {
    if (!editor) return;
    reviewState.draftContent = String(editor.value || "");
    reviewState.lastRenderedMarkdown = reviewState.draftContent;
    if (persist) persistDraftToLocal("manual_apply");
    updateSubmitButtonText(false);
  };

  const setEditingMode = (editing) => {
    const hasDraft = Boolean(String(reviewState.draftContent || "").trim());
    reviewState.isEditing = Boolean(editing && hasDraft);
    updateEditUI();
    if (reviewState.isEditing && editor) {
      editor.focus();
      editor.setSelectionRange(editor.value.length, editor.value.length);
    }
  };

  const toggleCustomRange = () => {
    const isCustom = String(form.range_type?.value || "day") === "custom";
    customRangeFields?.classList.toggle("is-hidden", !isCustom);
    if (form.start_date) form.start_date.required = isCustom;
    if (form.end_date) form.end_date.required = isCustom;
  };

  const invalidateDraftByFilterChange = (reason) => {
    clearDraftCache();
    reviewState.lastReviewResponse = null;
    reviewState.filterKey = buildReviewFilterKey(form);
    if (String(reviewState.draftContent || "").trim()) {
      setDraftContent("", { persist: false, keepEditing: false });
    }
    submitStatus.textContent = `${reason}已变化，旧复盘草稿已清除，请重新生成。`;
    submitStatus.classList.remove("is-error");
  };

  const triggerAutoContextLoad = () => {
    return loadAutoContext(form);
  };

  form.range_type?.addEventListener("change", async () => {
    const prevKey = reviewState.filterKey;
    toggleCustomRange();
    const nextKey = buildReviewFilterKey(form);
    if (prevKey !== nextKey) {
      invalidateDraftByFilterChange("总结范围");
    }
    await triggerAutoContextLoad();
  });
  form.date?.addEventListener("change", async () => {
    const prevKey = reviewState.filterKey;
    const nextKey = buildReviewFilterKey(form);
    if (prevKey !== nextKey) {
      invalidateDraftByFilterChange("基准日期");
    }
    await triggerAutoContextLoad();
  });
  form.start_date?.addEventListener("change", async () => {
    const prevKey = reviewState.filterKey;
    const nextKey = buildReviewFilterKey(form);
    if (prevKey !== nextKey) {
      invalidateDraftByFilterChange("自定义日期范围");
    }
    await triggerAutoContextLoad();
  });
  form.end_date?.addEventListener("change", async () => {
    const prevKey = reviewState.filterKey;
    const nextKey = buildReviewFilterKey(form);
    if (prevKey !== nextKey) {
      invalidateDraftByFilterChange("自定义日期范围");
    }
    await triggerAutoContextLoad();
  });

  form.querySelectorAll("textarea[data-autoresize]").forEach((textarea) => {
    autoResizeTextarea(textarea);
    textarea.addEventListener("input", () => autoResizeTextarea(textarea));
  });

  editor?.addEventListener("input", () => {
    applyEditorValueToState({ persist: false });
    scheduleDraftPersist();
  });

  toggleEditBtn?.addEventListener("click", () => {
    if (!reviewState.draftContent.trim()) return;
    setEditingMode(true);
  });

  exitEditBtn?.addEventListener("click", () => {
    setEditingMode(false);
    submitStatus.textContent = "已退出编辑模式。";
    submitStatus.classList.remove("is-error");
  });

  applyEditBtn?.addEventListener("click", () => {
    applyEditorValueToState({ persist: true });
    setEditingMode(false);
    submitStatus.textContent = "编辑内容已更新并缓存。";
    submitStatus.classList.remove("is-error");
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const rangeType = String(form.range_type?.value || "day");
    const anchorDate = String(form.date?.value || today).trim();
    const latestAutoContext = await loadAutoContext(form);
    if (!latestAutoContext) {
      submitStatus.textContent = "无法获取最新的系统关联任务数据，请修复时间范围后重试。";
      submitStatus.classList.add("is-error");
      return;
    }

    const payload = {
      range_type: rangeType,
      date: anchorDate,
      focus_text: String(form.focus_text?.value || "").trim(),
      done_text: String(form.done_text?.value || "").trim(),
      undone_text: String(form.undone_text?.value || "").trim(),
      blockers_text: String(form.blockers_text?.value || "").trim(),
      persist: false,
      ...buildFrontendObjectivePayload(latestAutoContext),
    };
    if (rangeType === "custom") {
      const startDate = String(form.start_date?.value || "").trim();
      const endDate = String(form.end_date?.value || "").trim();
      if (!startDate || !endDate) {
        submitStatus.textContent = "请先填写完整的自定义时间范围。";
        submitStatus.classList.add("is-error");
        return;
      }
      payload.start_date = startDate;
      payload.end_date = endDate;
    }

    submitBtn.disabled = true;
    updateSubmitButtonText(true);
    submitStatus.textContent = "AI 正在生成复盘总结，请稍候...";
    submitStatus.classList.remove("is-error");

    try {
      const data = await postJson("/api/review", payload);
      reviewState.lastReviewResponse = data;
      reviewState.filterKey = buildReviewFilterKey(form);
      const markdown = buildReviewMarkdown(data);
      setDraftContent(markdown, { persist: true, keepEditing: false });
      submitStatus.textContent = "复盘总结已生成，草稿已自动缓存。";
      submitStatus.classList.remove("is-error");
    } catch (err) {
      submitStatus.textContent = `生成失败：${String(err)}`;
      submitStatus.classList.add("is-error");
    } finally {
      submitBtn.disabled = false;
      updateSubmitButtonText(false);
    }
  });

  copyBtn?.addEventListener("click", async () => {
    const content = String(reviewState.draftContent || "").trim();
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      submitStatus.textContent = "复盘内容已复制到剪贴板。";
      submitStatus.classList.remove("is-error");
    } catch {
      submitStatus.textContent = "复制失败，请检查浏览器权限。";
      submitStatus.classList.add("is-error");
    }
  });

  saveBtn?.addEventListener("click", async () => {
    const content = String(reviewState.draftContent || "").trim();
    if (!content) return;
    if (reviewState.isEditing) {
      applyEditorValueToState({ persist: true });
      setEditingMode(false);
    }

    saveBtn.disabled = true;
    saveBtn.textContent = "保存中...";
    try {
      const anchorDate = String(reviewState.lastReviewResponse?.date || form.date?.value || todayDateKey()).trim();
      await saveReviewRecord(anchorDate);
      clearDraftCache();
      submitStatus.textContent = "复盘记录已保存，本地草稿已清理。";
      submitStatus.classList.remove("is-error");

      const shouldImport = window.confirm("是否将复盘中的【下一步行动】自动导入为明天的待办任务？");
      if (shouldImport) {
        const result = await importActionsToTomorrowTasks(
          anchorDate,
          String(reviewState.lastReviewResponse?.period_label || "今日")
        );
        if (result.imported) {
          submitStatus.textContent = `复盘已保存，且已将 ${result.count} 条下一步行动导入 ${result.tomorrow} 的待办任务。`;
        } else if (result.reason === "no_actions") {
          submitStatus.textContent = "复盘已保存，但未识别到可导入的下一步行动。";
        }
      }

      const goRecords = window.confirm("保存成功，是否前往「复盘记录」页面查看？");
      if (goRecords) {
        window.location.href = "/review/records";
      }
    } catch (err) {
      submitStatus.textContent = `保存失败：${String(err)}`;
      submitStatus.classList.add("is-error");
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "保存复盘记录";
    }
  });

  const restored = restoreDraftFromLocal();
  if (restored?.content && (!restored.filterKey || restored.filterKey === buildReviewFilterKey(form))) {
    reviewState.lastReviewResponse = restored.response;
    setDraftContent(restored.content, { persist: false, keepEditing: false });
    submitStatus.textContent = "检测到未保存的复盘草稿，已为您恢复。";
    submitStatus.classList.remove("is-error");
  } else {
    if (restored?.content) {
      clearDraftCache();
    }
    renderReviewResult("");
    updateEditUI();
    updateSubmitButtonText(false);
  }

  toggleCustomRange();
  triggerAutoContextLoad();
}

bindReviewPage();
