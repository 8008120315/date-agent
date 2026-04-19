const draftState = {
  draftTasks: [],
  selectedTaskIds: new Set(),
  expandedTaskIds: new Set(),
  removingTaskIds: new Set(),
  rangeDates: [],
  targetDate: "",
  goalText: "",
  existingTasksByDate: new Map(),
  freshTaskIndicesByDate: new Map(),
  freshTimersByDate: new Map(),
};

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

function enumerateDateRange(startDateKey, endDateKey) {
  const start = parseDateKey(startDateKey);
  const end = parseDateKey(endDateKey);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) {
    return [];
  }
  const rows = [];
  let cursor = new Date(start.getFullYear(), start.getMonth(), start.getDate());
  while (cursor <= end && rows.length < 90) {
    rows.push(toDateKey(cursor));
    cursor = addDays(cursor, 1);
  }
  return rows;
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

function setDateLoadStatus(message, { error = false } = {}) {
  const el = document.getElementById("draft-date-load-status");
  if (!el) return;
  el.textContent = message;
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
    assignBtn.disabled = selected === 0 || !draftState.targetDate;
  }
  updateSelectToggleButton();
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

function renderTargetDateOptions() {
  const select = document.getElementById("draft-target-date-select");
  if (!select) return;
  select.innerHTML = draftState.rangeDates
    .map((dateKey) => `<option value="${escapeHtml(dateKey)}">${escapeHtml(dateKey)}</option>`)
    .join("");
  if (!draftState.rangeDates.length) {
    draftState.targetDate = "";
    select.disabled = true;
    select.innerHTML = `<option value="">请先生成草稿</option>`;
  } else {
    if (!draftState.rangeDates.includes(draftState.targetDate)) {
      draftState.targetDate = draftState.rangeDates[0];
    }
    select.value = draftState.targetDate;
    select.disabled = false;
  }
}

function renderInboxMeta() {
  const meta = document.getElementById("draft-inbox-meta");
  if (!meta) return;
  if (!draftState.draftTasks.length) {
    meta.textContent = "当前任务池为空，先生成草稿任务或重新生成。";
    return;
  }
  const selected = countSelectedDrafts();
  meta.textContent = `任务池共 ${draftState.draftTasks.length} 条草稿，已勾选 ${selected} 条。`;
}

function renderDraftPool() {
  const list = document.getElementById("draft-pool-list");
  if (!list) return;
  if (!draftState.draftTasks.length) {
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
        : `<li class="hint">暂无子任务</li>`;

      return `
        <article class="draft-task-card ${removing ? "is-removing" : ""}" data-draft-id="${escapeHtml(task.draft_id)}">
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
            <p class="draft-task-desc">${escapeHtml(task.done_definition || "有明确可验证产出")}</p>
            <ul class="draft-subtasks">${checklistHtml}</ul>
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
  summary.textContent = `该日已有任务：${tasks.length} 个 | 预计总耗时：${Number(totalHours.toFixed(1))} h`;

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
    setDateLoadStatus("请选择目标日期以查看当日任务负载。");
    renderExistingTasksBoard();
    return;
  }
  setDateLoadStatus("正在加载该日期已有任务...");
  try {
    await fetchTasksByDate(draftState.targetDate);
    renderExistingTasksBoard();
    setDateLoadStatus("已加载该日期任务负载。");
  } catch (err) {
    setDateLoadStatus(`加载失败：${String(err)}`, { error: true });
    renderExistingTasksBoard();
  }
}

function applyRangeToInputs(startDate, endDate) {
  const startInput = document.getElementById("draft-start-date");
  const endInput = document.getElementById("draft-end-date");
  if (startInput) startInput.value = startDate;
  if (endInput) endInput.value = endDate;
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

function bindGeneratePage() {
  const form = document.getElementById("draft-plan-form");
  if (!form) return;

  const startInput = document.getElementById("draft-start-date");
  const endInput = document.getElementById("draft-end-date");
  const goalInput = document.getElementById("draft-goal-text");
  const generateBtn = document.getElementById("draft-generate-btn");
  const targetDateSelect = document.getElementById("draft-target-date-select");
  const toggleSelectBtn = document.getElementById("draft-toggle-select-btn");
  const clearDraftBtn = document.getElementById("draft-clear-btn");
  const assignBtn = document.getElementById("draft-assign-btn");
  const poolList = document.getElementById("draft-pool-list");

  const today = toDateKey(new Date());
  const defaultEnd = toDateKey(addDays(new Date(), 2));
  applyRangeToInputs(today, defaultEnd);
  draftState.rangeDates = enumerateDateRange(today, defaultEnd);
  draftState.targetDate = draftState.rangeDates[0] || "";
  renderTargetDateOptions();
  renderDraftPool();
  renderExistingTasksBoard();
  void loadExistingTasksForTargetDate();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const startDate = String(startInput?.value || "").trim();
    const endDate = String(endInput?.value || "").trim();
    const goalText = String(goalInput?.value || "").trim();

    const rangeDates = enumerateDateRange(startDate, endDate);
    if (!rangeDates.length) {
      setGenerateStatus("日期范围无效，请重新选择。", { error: true });
      return;
    }
    if (!goalText) {
      setGenerateStatus("请先输入目标。", { error: true });
      return;
    }

    generateBtn.disabled = true;
    generateBtn.textContent = "生成中...";
    setGenerateStatus("AI 正在生成多日任务草稿池...");

    try {
      const data = await postJson("/api/plan/draft/generate", {
        start_date: startDate,
        end_date: endDate,
        goal_text: goalText,
      });
      console.log("[draft generate]", data);
      draftState.goalText = goalText;
      draftState.draftTasks = (Array.isArray(data.draft_tasks) ? data.draft_tasks : []).map(normalizeTask);
      draftState.selectedTaskIds.clear();
      draftState.expandedTaskIds.clear();
      draftState.removingTaskIds.clear();
      draftState.rangeDates = enumerateDateRange(
        String(data.start_date || startDate),
        String(data.end_date || endDate),
      );
      draftState.targetDate = draftState.rangeDates[0] || "";

      renderTargetDateOptions();
      renderDraftPool();
      renderExistingTasksBoard();
      void loadExistingTasksForTargetDate();
      setGenerateStatus(`已生成 ${draftState.draftTasks.length} 条草稿任务，可手动分配到目标日期。`);
      showToast(`草稿池已生成（${draftState.draftTasks.length} 条）`);
    } catch (err) {
      setGenerateStatus(`生成失败：${String(err)}`, { error: true });
      showToast(`生成失败：${String(err)}`, { error: true });
    } finally {
      generateBtn.disabled = false;
      generateBtn.textContent = "生成任务草稿池";
    }
  });

  targetDateSelect?.addEventListener("change", async () => {
    draftState.targetDate = String(targetDateSelect.value || "").trim();
    updateAssignSummary();
    renderExistingTasksBoard();
    await loadExistingTasksForTargetDate();
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
      setDateLoadStatus("该日期任务已更新。");

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
