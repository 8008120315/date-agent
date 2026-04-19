const draftState = {
  draftTasks: [],
  selectedTaskIds: new Set(),
  expandedTaskIds: new Set(),
  removingTaskIds: new Set(),
  enrichingTaskIds: new Set(),
  targetDate: "",
  goalText: "",
  isGenerating: false,
  streamAbortController: null,
  loadingHintTimer: null,
  loadingHintIndex: 0,
  existingTasksByDate: new Map(),
  freshTaskIndicesByDate: new Map(),
  freshTimersByDate: new Map(),
};

const GENERATE_HINTS = [
  "正在理解您的目标...",
  "正在调用专家经验拆解步骤...",
  "正在评估任务耗时与优先级...",
  "即将完成...",
];

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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

function isPastDate(dateKey) {
  if (!dateKey) return false;
  return dateKey < todayDateKey();
}

function normalizeHours(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  return Number(n.toFixed(1));
}

function normalizeChecklist(value) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      const content = String(item?.content || "").trim();
      if (!content) return null;
      return { content, is_done: Boolean(item?.is_done) };
    })
    .filter(Boolean);
}

function normalizeTask(task) {
  return {
    ...task,
    draft_id: String(task?.draft_id || "").trim(),
    title: String(task?.title || "").trim(),
    priority: String(task?.priority || "P2").trim().toUpperCase() || "P2",
    estimate_hours: normalizeHours(task?.estimate_hours),
    done_definition: String(task?.done_definition || "").trim(),
    checklist: normalizeChecklist(task?.checklist),
  };
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

function postJson(url, payload) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await readErrorMessage(response));
    }
    return response.json();
  });
}

function getJson(url) {
  return fetch(url).then(async (response) => {
    if (!response.ok) {
      throw new Error(await readErrorMessage(response));
    }
    return response.json();
  });
}

function ensureToastContainer() {
  let container = document.getElementById("global-toast-container");
  if (container) return container;
  container = document.createElement("div");
  container.id = "global-toast-container";
  container.className = "toast-container";
  document.body.appendChild(container);
  return container;
}

function showToast(message, { error = false } = {}) {
  const container = ensureToastContainer();
  const item = document.createElement("div");
  item.className = `toast-item${error ? " is-error" : ""}`;
  item.textContent = String(message || "").trim() || (error ? "操作失败" : "操作成功");
  container.appendChild(item);
  setTimeout(() => {
    item.classList.add("is-leaving");
    setTimeout(() => item.remove(), 260);
  }, 2200);
}

function setGenerateStatus(message, { error = false } = {}) {
  const el = document.getElementById("draft-generate-status");
  if (!el) return;
  el.textContent = message;
  el.classList.toggle("is-error", error);
}

function startGenerateHintRotation() {
  if (draftState.loadingHintTimer) {
    clearInterval(draftState.loadingHintTimer);
  }
  draftState.loadingHintIndex = 0;
  setGenerateStatus(GENERATE_HINTS[0]);
  draftState.loadingHintTimer = setInterval(() => {
    if (!draftState.isGenerating) return;
    draftState.loadingHintIndex = (draftState.loadingHintIndex + 1) % GENERATE_HINTS.length;
    setGenerateStatus(GENERATE_HINTS[draftState.loadingHintIndex]);
  }, 2000);
}

function stopGenerateHintRotation() {
  if (draftState.loadingHintTimer) {
    clearInterval(draftState.loadingHintTimer);
    draftState.loadingHintTimer = null;
  }
}

async function consumeSSE(url, payload, handlers = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(payload),
    signal: draftState.streamAbortController?.signal,
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  if (!response.body) {
    throw new Error("浏览器不支持流式读取");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const dispatchEvent = (eventName, rawData) => {
    let data = rawData;
    if (typeof rawData === "string" && rawData.trim()) {
      try {
        data = JSON.parse(rawData);
      } catch {
        data = { message: rawData };
      }
    }
    if (handlers.onEvent) {
      handlers.onEvent(eventName, data);
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundaryIndex = buffer.indexOf("\n\n");
    while (boundaryIndex !== -1) {
      const block = buffer.slice(0, boundaryIndex).trim();
      buffer = buffer.slice(boundaryIndex + 2);
      boundaryIndex = buffer.indexOf("\n\n");
      if (!block) continue;

      let eventName = "message";
      const dataLines = [];
      block.split(/\r?\n/).forEach((line) => {
        if (!line || line.startsWith(":")) return;
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim() || "message";
          return;
        }
        if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trimStart());
        }
      });
      dispatchEvent(eventName, dataLines.join("\n"));
    }
  }
}

function setDateLoadStatus(message, { error = false } = {}) {
  const el = document.getElementById("draft-date-load-status");
  if (!el) return;
  const text = String(message || "").trim();
  el.textContent = text;
  el.hidden = !text;
  el.classList.toggle("is-error", error);
}

function countSelectedDrafts() {
  let total = 0;
  draftState.selectedTaskIds.forEach((id) => {
    if (draftState.draftTasks.some((task) => task.draft_id === id)) {
      total += 1;
    }
  });
  return total;
}

function updateSelectToggleButton() {
  const button = document.getElementById("draft-toggle-select-btn");
  if (!button) return;
  const selectable = draftState.draftTasks.filter((task) => !draftState.removingTaskIds.has(task.draft_id)).length;
  const selected = countSelectedDrafts();
  button.textContent = selectable > 0 && selected === selectable ? "取消全选" : "全选";
  button.disabled = selectable === 0;
}

function updateAssignSummary() {
  const selected = countSelectedDrafts();
  const summary = document.getElementById("draft-selected-summary");
  const assignBtn = document.getElementById("draft-assign-btn");
  if (summary) {
    summary.textContent = `已选择 ${selected} 个任务`;
  }
  if (assignBtn) {
    assignBtn.textContent = `将勾选的 ${selected} 个任务加入该日期`;
    assignBtn.disabled = selected === 0 || !draftState.targetDate || isPastDate(draftState.targetDate);
  }
  updateSelectToggleButton();
}

function updateQuickDateButtons() {
  const buttons = Array.from(document.querySelectorAll(".draft-quick-date-btn"));
  const today = parseDateKey(todayDateKey());
  const target = parseDateKey(draftState.targetDate);
  const diffDays = Number.isNaN(target.getTime())
    ? null
    : Math.round((target.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));

  buttons.forEach((btn) => {
    const offset = Number(btn.getAttribute("data-offset") || 0);
    btn.classList.toggle("is-active", diffDays === offset);
  });
}

function taskKey(task) {
  return `${String(task?.title || "").trim()}|${String(task?.priority || "P2").trim()}|${normalizeHours(task?.estimate_hours) ?? ""}`;
}

function markFreshTasks(dateKey, allTasks, appendedTasks) {
  if (!dateKey) return;
  const countMap = new Map();
  appendedTasks.forEach((row) => {
    const key = taskKey(row);
    countMap.set(key, (countMap.get(key) || 0) + 1);
  });
  const freshSet = new Set();
  allTasks.forEach((row, idx) => {
    const key = taskKey(row);
    const left = countMap.get(key) || 0;
    if (left > 0) {
      freshSet.add(idx);
      countMap.set(key, left - 1);
    }
  });
  draftState.freshTaskIndicesByDate.set(dateKey, freshSet);

  const oldTimer = draftState.freshTimersByDate.get(dateKey);
  if (oldTimer) {
    clearTimeout(oldTimer);
  }
  const timer = setTimeout(() => {
    draftState.freshTaskIndicesByDate.delete(dateKey);
    draftState.freshTimersByDate.delete(dateKey);
    if (draftState.targetDate === dateKey) {
      renderExistingTasksBoard();
    }
  }, 2600);
  draftState.freshTimersByDate.set(dateKey, timer);
}

function renderInboxMeta() {
  const meta = document.getElementById("draft-inbox-meta");
  if (!meta) return;
  if (draftState.isGenerating) {
    meta.textContent = draftState.draftTasks.length
      ? `已流式生成 ${draftState.draftTasks.length} 条草稿任务，仍在继续...`
      : "正在流式生成任务草稿，任务将逐条出现。";
    return;
  }
  if (!draftState.draftTasks.length) {
    meta.textContent = "当前任务池为空，输入目标后生成任务草稿。";
    return;
  }
  const selected = countSelectedDrafts();
  meta.textContent = `任务池共 ${draftState.draftTasks.length} 条草稿，已勾选 ${selected} 条。`;
}

function renderDraftPool() {
  const list = document.getElementById("draft-pool-list");
  if (!list) return;
  if (!draftState.draftTasks.length) {
    if (draftState.isGenerating) {
      list.innerHTML = `
        <div class="draft-skeleton-list" aria-live="polite" aria-busy="true">
          ${new Array(4)
            .fill(0)
            .map(
              () => `
            <article class="draft-skeleton-card">
              <div class="draft-skeleton-line w-60"></div>
              <div class="draft-skeleton-line w-30"></div>
              <div class="draft-skeleton-line w-90"></div>
            </article>
          `,
            )
            .join("")}
        </div>
      `;
      renderInboxMeta();
      updateAssignSummary();
      return;
    }
    list.innerHTML = `
      <div class="draft-empty-state">
        <p>暂无草稿任务</p>
        <p class="hint">输入目标并生成后，任务会出现在这里。</p>
      </div>
    `;
    renderInboxMeta();
    updateAssignSummary();
    return;
  }

  list.innerHTML = draftState.draftTasks
    .map((task) => {
      const checked = draftState.selectedTaskIds.has(task.draft_id);
      const removing = draftState.removingTaskIds.has(task.draft_id);
      const expanded = draftState.expandedTaskIds.has(task.draft_id);
      const enriching = draftState.enrichingTaskIds.has(task.draft_id);
      const estimate = task.estimate_hours ? `${task.estimate_hours} h` : "未估时";
      const checklist = Array.isArray(task.checklist) ? task.checklist : [];
      const checklistHtml = checklist.length
        ? checklist
            .map(
              (row) => `
            <li>
              <span class="token-check ${row.is_done ? "is-done" : ""}">${row.is_done ? "✓" : ""}</span>
              <span>${escapeHtml(row.content)}</span>
            </li>
          `,
            )
            .join("")
        : `<li class="hint">暂无子任务，展开后将按需补全。</li>`;

      const collapseBody = enriching
        ? `
          <div class="draft-enriching-state">
            <span class="draft-enrich-dot"></span>
            <span class="hint">正在为该任务生成子任务...</span>
          </div>
        `
        : `
          <p class="draft-task-desc">${escapeHtml(task.done_definition || "有明确可验证产出")}</p>
          <ul class="draft-subtasks">${checklistHtml}</ul>
        `;

      return `
        <article class="draft-task-card ${removing ? "is-removing" : ""} ${enriching ? "is-enriching" : ""}" data-draft-id="${escapeHtml(task.draft_id)}">
          <div class="draft-task-head">
            <label class="draft-task-check">
              <input data-role="draft-select" type="checkbox" value="${escapeHtml(task.draft_id)}" ${checked ? "checked" : ""} ${
        removing ? "disabled" : ""
      } />
            </label>
            <button data-role="toggle-expand" type="button" class="draft-task-toggle" aria-expanded="${expanded ? "true" : "false"}">
              <div class="draft-task-topline">
                <h4>${escapeHtml(task.title || "未命名任务")}</h4>
                <span class="draft-expand-arrow ${expanded ? "is-open" : ""}">⌄</span>
              </div>
              <div class="draft-task-meta">
                <span class="task-priority">${escapeHtml(task.priority || "P2")}</span>
                <span class="task-estimate">${escapeHtml(estimate)}</span>
              </div>
            </button>
          </div>
          <div class="draft-task-collapse ${expanded ? "is-open" : ""}">
            ${collapseBody}
          </div>
        </article>
      `;
    })
    .join("");

  renderInboxMeta();
  updateAssignSummary();
}

function renderExistingTasksBoard() {
  const list = document.getElementById("draft-existing-list");
  const summary = document.getElementById("draft-load-summary");
  if (!list || !summary) return;

  const dateKey = draftState.targetDate;
  const tasks = draftState.existingTasksByDate.get(dateKey) || [];
  const totalHours = tasks.reduce((sum, task) => sum + (normalizeHours(task.estimate_hours) || 0), 0);
  const roundedHours = Number(totalHours.toFixed(1));
  const overloadClass = roundedHours >= 8 ? " is-overload" : "";
  summary.innerHTML = `
    <span class="draft-load-count">该日已有任务：${tasks.length} 个</span>
    <span class="draft-load-hours${overloadClass}">预计总耗时：${roundedHours} h</span>
  `;

  if (!tasks.length) {
    list.innerHTML = `
      <div class="draft-existing-empty">
        <p>该日期暂无正式任务</p>
        <p class="hint">适合把草稿任务分配到这一天。</p>
      </div>
    `;
    return;
  }

  const freshSet = draftState.freshTaskIndicesByDate.get(dateKey) || new Set();
  list.innerHTML = tasks
    .map((task, idx) => {
      const estimate = task.estimate_hours ? `${task.estimate_hours} h` : "未估时";
      const freshClass = freshSet.has(idx) ? " is-fresh" : "";
      return `
        <article class="draft-existing-item${freshClass}">
          <span class="draft-existing-title">${escapeHtml(task.title || "未命名任务")}</span>
          <span class="draft-existing-meta">${escapeHtml(estimate)}</span>
        </article>
      `;
    })
    .join("");
}

async function fetchTasksByDate(dateKey) {
  // 预留占位：如未来改为专用接口，可在此替换实现。
  const data = await getJson(`/api/plan/day?date=${encodeURIComponent(dateKey)}`);
  const planItems = Array.isArray(data?.plan_items) ? data.plan_items : [];
  const normalized = planItems.map(normalizeTask);
  draftState.existingTasksByDate.set(dateKey, normalized);
  return normalized;
}

async function loadExistingTasksForTargetDate() {
  if (!draftState.targetDate) {
    setDateLoadStatus("");
    renderExistingTasksBoard();
    return;
  }
  if (isPastDate(draftState.targetDate)) {
    setDateLoadStatus("目标日期不能早于今天。", { error: true });
    renderExistingTasksBoard();
    return;
  }
  try {
    await fetchTasksByDate(draftState.targetDate);
    renderExistingTasksBoard();
    setDateLoadStatus("");
  } catch (err) {
    setDateLoadStatus(`加载失败：${String(err)}`, { error: true });
    renderExistingTasksBoard();
  }
}

async function removeAssignedDraftsWithAnimation(draftIds) {
  draftState.removingTaskIds = new Set(draftIds);
  renderDraftPool();
  await new Promise((resolve) => setTimeout(resolve, 260));
  const idSet = new Set(draftIds);
  draftState.draftTasks = draftState.draftTasks.filter((task) => !idSet.has(task.draft_id));
  draftIds.forEach((id) => {
    draftState.selectedTaskIds.delete(id);
    draftState.expandedTaskIds.delete(id);
  });
  draftState.removingTaskIds.clear();
  renderDraftPool();
}

function applyQuickDateSelection(targetInput, offset) {
  const base = new Date();
  const target = toDateKey(addDays(base, Number(offset || 0)));
  targetInput.value = target;
  draftState.targetDate = target;
  updateAssignSummary();
  updateQuickDateButtons();
  renderExistingTasksBoard();
  void loadExistingTasksForTargetDate();
}

function bindGeneratePage() {
  const form = document.getElementById("draft-plan-form");
  if (!form) return;

  const goalInput = document.getElementById("draft-goal-text");
  const generateBtn = document.getElementById("draft-generate-btn");
  const targetDateInput = document.getElementById("draft-target-date-input");
  const quickDateButtons = Array.from(document.querySelectorAll(".draft-quick-date-btn"));
  const toggleSelectBtn = document.getElementById("draft-toggle-select-btn");
  const clearDraftBtn = document.getElementById("draft-clear-btn");
  const assignBtn = document.getElementById("draft-assign-btn");
  const poolList = document.getElementById("draft-pool-list");

  if (!(targetDateInput instanceof HTMLInputElement)) return;

  const today = todayDateKey();
  targetDateInput.min = today;
  targetDateInput.value = today;
  draftState.targetDate = today;
  updateQuickDateButtons();
  renderDraftPool();
  renderExistingTasksBoard();
  void loadExistingTasksForTargetDate();

  const setGeneratingState = (isGenerating) => {
    draftState.isGenerating = Boolean(isGenerating);
    if (generateBtn) {
      generateBtn.disabled = draftState.isGenerating;
      generateBtn.textContent = draftState.isGenerating ? "生成中..." : "生成任务草稿池";
    }
    if (draftState.isGenerating) {
      startGenerateHintRotation();
    } else {
      stopGenerateHintRotation();
    }
    renderInboxMeta();
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const goalText = String(goalInput?.value || "").trim();

    if (!goalText) {
      setGenerateStatus("请先输入目标。", { error: true });
      return;
    }

    if (draftState.streamAbortController) {
      draftState.streamAbortController.abort();
      draftState.streamAbortController = null;
    }

    draftState.goalText = goalText;
    draftState.draftTasks = [];
    draftState.selectedTaskIds.clear();
    draftState.expandedTaskIds.clear();
    draftState.removingTaskIds.clear();
    draftState.enrichingTaskIds.clear();
    renderDraftPool();
    renderExistingTasksBoard();
    setGeneratingState(true);

    draftState.streamAbortController = new AbortController();
    let completePayload = null;
    let streamErrorMessage = "";
    let firstTaskReceived = false;

    try {
      await consumeSSE("/api/plan/draft/generate/stream", {
        goal_text: goalText,
      }, {
        onEvent: (eventName, data) => {
          if (eventName === "start") {
            if (!firstTaskReceived && data?.message) {
              setGenerateStatus(String(data.message));
            }
            return;
          }
          if (eventName === "status") {
            if (firstTaskReceived && data?.message) {
              setGenerateStatus(String(data.message));
            }
            return;
          }
          if (eventName === "task" && data?.task) {
            if (!firstTaskReceived) {
              firstTaskReceived = true;
              stopGenerateHintRotation();
            }
            const normalized = normalizeTask(data.task);
            if (!normalized.draft_id) return;
            if (draftState.draftTasks.some((task) => task.draft_id === normalized.draft_id)) return;
            draftState.draftTasks.push(normalized);
            renderDraftPool();
            renderExistingTasksBoard();
            const index = Number(data.index || draftState.draftTasks.length);
            const total = Number(data.total || 0);
            setGenerateStatus(total > 0 ? `已生成 ${index}/${total} 条草稿任务` : `已生成 ${index} 条草稿任务`);
            return;
          }
          if (eventName === "complete") {
            completePayload = data || {};
            return;
          }
          if (eventName === "error") {
            streamErrorMessage = String(data?.message || "生成失败");
          }
        },
      });
      if (streamErrorMessage) {
        throw new Error(streamErrorMessage);
      }
      const spanDays = Number(completePayload?.inferred_span_days || 0);
      const spanTip = spanDays > 0 ? `（推断周期约 ${spanDays} 天）` : "";
      setGenerateStatus(`已生成 ${draftState.draftTasks.length} 条草稿任务 ${spanTip}`.trim());
      showToast(`草稿池已生成（${draftState.draftTasks.length} 条）`);
    } catch (err) {
      setGenerateStatus(`生成失败：${String(err)}`, { error: true });
      showToast(`生成失败：${String(err)}`, { error: true });
    } finally {
      draftState.streamAbortController = null;
      setGeneratingState(false);
    }
  });

  targetDateInput.addEventListener("change", async () => {
    const nextDate = String(targetDateInput.value || "").trim();
    if (!nextDate) {
      draftState.targetDate = "";
      updateAssignSummary();
      updateQuickDateButtons();
      renderExistingTasksBoard();
      setDateLoadStatus("");
      return;
    }
    if (isPastDate(nextDate)) {
      targetDateInput.value = todayDateKey();
      draftState.targetDate = targetDateInput.value;
      updateAssignSummary();
      updateQuickDateButtons();
      renderExistingTasksBoard();
      setDateLoadStatus("目标日期不能早于今天。", { error: true });
      showToast("只能分配到今天及未来日期", { error: true });
      return;
    }
    draftState.targetDate = nextDate;
    updateAssignSummary();
    updateQuickDateButtons();
    renderExistingTasksBoard();
    await loadExistingTasksForTargetDate();
  });

  quickDateButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const offset = Number(btn.getAttribute("data-offset") || 0);
      applyQuickDateSelection(targetDateInput, offset);
    });
  });

  poolList?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.getAttribute("data-role") !== "draft-select") return;
    const draftId = String(target.value || "").trim();
    if (!draftId) return;
    if (target.checked) {
      draftState.selectedTaskIds.add(draftId);
    } else {
      draftState.selectedTaskIds.delete(draftId);
    }
    updateAssignSummary();
    renderInboxMeta();
  });

  poolList?.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const toggleButton = target.closest('[data-role="toggle-expand"]');
    if (!toggleButton) return;
    const card = toggleButton.closest(".draft-task-card");
    if (!card) return;
    const draftId = String(card.getAttribute("data-draft-id") || "").trim();
    if (!draftId) return;
    if (draftState.expandedTaskIds.has(draftId)) {
      draftState.expandedTaskIds.delete(draftId);
    } else {
      draftState.expandedTaskIds.add(draftId);
      const currentTask = draftState.draftTasks.find((task) => task.draft_id === draftId);
      const hasChecklist = Array.isArray(currentTask?.checklist) && currentTask.checklist.length > 0;
      const isEnriching = draftState.enrichingTaskIds.has(draftId);
      if (currentTask && !hasChecklist && !isEnriching) {
        draftState.enrichingTaskIds.add(draftId);
        renderDraftPool();
        void postJson("/api/plan/draft/item/enrich", {
          goal_text: draftState.goalText,
          task: currentTask,
        })
          .then((data) => {
            const incoming = normalizeTask(data?.task || {});
            if (!incoming.draft_id) return;
            const idx = draftState.draftTasks.findIndex((task) => task.draft_id === incoming.draft_id);
            if (idx < 0) return;
            draftState.draftTasks[idx] = {
              ...draftState.draftTasks[idx],
              ...incoming,
            };
          })
          .catch((err) => {
            showToast(`补全子任务失败：${String(err)}`, { error: true });
          })
          .finally(() => {
            draftState.enrichingTaskIds.delete(draftId);
            renderDraftPool();
          });
      }
    }
    renderDraftPool();
  });

  toggleSelectBtn?.addEventListener("click", () => {
    const selectableIds = draftState.draftTasks
      .filter((task) => !draftState.removingTaskIds.has(task.draft_id))
      .map((task) => task.draft_id);
    const selected = countSelectedDrafts();
    if (selectableIds.length > 0 && selected === selectableIds.length) {
      draftState.selectedTaskIds.clear();
    } else {
      selectableIds.forEach((id) => draftState.selectedTaskIds.add(id));
    }
    renderDraftPool();
  });

  clearDraftBtn?.addEventListener("click", () => {
    if (!draftState.draftTasks.length) return;
    if (!window.confirm("确定清空剩余草稿任务吗？")) return;
    draftState.draftTasks = [];
    draftState.selectedTaskIds.clear();
    draftState.expandedTaskIds.clear();
    draftState.removingTaskIds.clear();
    renderDraftPool();
    showToast("已清空剩余草稿");
  });

  assignBtn?.addEventListener("click", async () => {
    const selectedIds = Array.from(draftState.selectedTaskIds);
    if (!selectedIds.length) {
      showToast("请先勾选至少一个任务", { error: true });
      return;
    }
    if (!draftState.targetDate) {
      showToast("请先选择目标日期", { error: true });
      return;
    }
    if (isPastDate(draftState.targetDate)) {
      showToast("只能分配到今天及未来日期", { error: true });
      return;
    }
    const selectedTasks = draftState.draftTasks.filter((task) => selectedIds.includes(task.draft_id));
    if (!selectedTasks.length) {
      showToast("已选任务不存在，请重新勾选", { error: true });
      return;
    }

    assignBtn.disabled = true;
    assignBtn.textContent = "分配中...";
    try {
      const data = await postJson("/api/plan/draft/assign", {
        target_date: draftState.targetDate,
        goal_text: draftState.goalText,
        tasks: selectedTasks,
      });
      console.log("[draft assign]", data);

      const planItems = (Array.isArray(data?.plan?.plan_items) ? data.plan.plan_items : []).map(normalizeTask);
      draftState.existingTasksByDate.set(draftState.targetDate, planItems);
      markFreshTasks(draftState.targetDate, planItems, selectedTasks);
      renderExistingTasksBoard();
      setDateLoadStatus("");

      await removeAssignedDraftsWithAnimation(selectedIds);
      showToast(`已成功将 ${selectedTasks.length} 个任务分配至 ${draftState.targetDate} 日程中`);
      renderInboxMeta();
    } catch (err) {
      showToast(`分配失败：${String(err)}`, { error: true });
    } finally {
      assignBtn.disabled = false;
      updateAssignSummary();
    }
  });
}

bindGeneratePage();
