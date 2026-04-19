const state = {
  focusDate: startOfDay(new Date()),
  plansByDate: new Map(),
  manualDraftsByDate: new Map(),
  reviewsByDate: new Map(),
  planResultDate: null,
  latestBoardRequestSeq: 0,
  splitRescheduleContext: null,
  splitModalBound: false,
};

const PRIORITY_ORDER = { P0: 0, P1: 1, P2: 2 };
let draftSeed = 1;

function showOutput(data) {
  const output = document.getElementById("output");
  if (!output) return;
  output.textContent = JSON.stringify(data, null, 2);
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
      // ignore json parse error
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
      const error = await readErrorMessage(response);
      throw new Error(error);
    }
    return response.json();
  });
}

function deleteJson(url) {
  return fetch(url, {
    method: "DELETE",
  }).then(async (response) => {
    if (!response.ok) {
      const error = await readErrorMessage(response);
      throw new Error(error);
    }
    return response.json();
  });
}

function putJson(url, payload) {
  return fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(async (response) => {
    if (!response.ok) {
      const error = await readErrorMessage(response);
      throw new Error(error);
    }
    return response.json();
  });
}

function getJson(url) {
  return fetch(url).then(async (response) => {
    if (!response.ok) {
      const error = await readErrorMessage(response);
      throw new Error(error);
    }
    return response.json();
  });
}

function startOfDay(dateObj) {
  return new Date(dateObj.getFullYear(), dateObj.getMonth(), dateObj.getDate());
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
  const d = startOfDay(dateObj);
  d.setDate(d.getDate() + delta);
  return d;
}

function ensureDate(value) {
  if (value) return value;
  return toDateKey(new Date());
}

function todayDateKey() {
  return toDateKey(new Date());
}

function isValidDateKey(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(String(value || "").trim());
}

function nextDraftId() {
  const id = `draft-${Date.now()}-${draftSeed}`;
  draftSeed += 1;
  return id;
}

function isPastDateKey(dateKey) {
  const target = String(dateKey || "").trim();
  if (!target) return false;
  return target < todayDateKey();
}

function resolvePlanSpanDays(spanType, rawCustomDays) {
  if (spanType === "week") return 7;
  if (spanType === "month") return 30;
  if (spanType === "custom") {
    const n = Number.parseInt(String(rawCustomDays || "").trim(), 10);
    if (!Number.isFinite(n) || n <= 0) return null;
    return Math.min(90, n);
  }
  return 1;
}

function normalizePriority(value) {
  const p = String(value || "").trim().toUpperCase();
  return p in PRIORITY_ORDER ? p : "P2";
}

function normalizeStatus(value) {
  const s = String(value || "").trim().toLowerCase();
  if (s === "todo" || s === "partial" || s === "done") {
    return s;
  }
  return "todo";
}

function normalizePercent(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function normalizeChecklistItem(raw) {
  if (!raw || typeof raw !== "object") {
    const text = String(raw || "").trim();
    return text ? { content: text, is_done: false } : null;
  }
  const content = String(raw.content || "").trim();
  if (!content) return null;
  return {
    content,
    is_done: Boolean(raw.is_done),
  };
}

function deriveStatusFromChecklist(checklist) {
  if (!Array.isArray(checklist) || checklist.length === 0) {
    return { status: "todo", percent: 0 };
  }
  const total = checklist.length;
  const done = checklist.filter((row) => Boolean(row.is_done)).length;
  if (done <= 0) return { status: "todo", percent: 0 };
  if (done >= total) return { status: "done", percent: 100 };
  return { status: "partial", percent: Math.max(1, Math.min(99, Math.round((done * 100) / total))) };
}

function normalizePlanItem(item) {
  const checklistRaw = Array.isArray(item?.checklist) ? item.checklist : [];
  let checklist = checklistRaw.map(normalizeChecklistItem).filter(Boolean);

  let status = normalizeStatus(item?.progress_status);
  let percent = normalizePercent(item?.progress_percent);

  if (!checklist.length) {
    if (status === "done") {
      percent = 100;
    } else if (status === "todo") {
      percent = 0;
    } else if (percent <= 0 || percent >= 100) {
      percent = 50;
    }
  } else if (status === "done") {
    checklist = checklist.map((row) => ({ ...row, is_done: true }));
    percent = 100;
  } else if (status === "todo") {
    checklist = checklist.map((row) => ({ ...row, is_done: false }));
    percent = 0;
  } else {
    const derived = deriveStatusFromChecklist(checklist);
    status = derived.status;
    percent = derived.percent;
  }

  return {
    ...item,
    title: String(item?.title || "").trim(),
    priority: normalizePriority(item?.priority),
    estimate_hours:
      Number.isFinite(Number(item?.estimate_hours)) && Number(item?.estimate_hours) > 0
        ? Number(item.estimate_hours)
        : null,
    done_definition: String(item?.done_definition || "").trim(),
    checklist,
    progress_status: status,
    progress_percent: percent,
    progress_note: String(item?.progress_note || ""),
  };
}

function sortPlanEntries(items) {
  return items
    .map((item, index) => ({ item: normalizePlanItem(item), index }))
    .sort((a, b) => {
      const rank = PRIORITY_ORDER[a.item.priority] - PRIORITY_ORDER[b.item.priority];
      if (rank !== 0) return rank;
      return String(a.item.title || "").localeCompare(String(b.item.title || ""), "zh-CN");
    });
}

function getManualDrafts(dateKey) {
  const rows = state.manualDraftsByDate.get(dateKey);
  return Array.isArray(rows) ? rows : [];
}

function setManualDrafts(dateKey, drafts) {
  state.manualDraftsByDate.set(dateKey, Array.isArray(drafts) ? drafts : []);
}

function removeManualDraft(dateKey, draftId) {
  const drafts = getManualDrafts(dateKey);
  setManualDrafts(
    dateKey,
    drafts.filter((row) => row._draft_id !== draftId),
  );
}

function prependManualDraft(dateKey) {
  const drafts = getManualDrafts(dateKey);
  const draftItem = normalizePlanItem({
    title: "",
    priority: "P2",
    estimate_hours: null,
    done_definition: "",
    checklist: [],
    progress_status: "todo",
    progress_percent: 0,
    progress_note: "",
  });
  draftItem._draft_id = nextDraftId();
  setManualDrafts(dateKey, [draftItem, ...drafts]);
  return draftItem;
}

function statusLabel(status, percent) {
  if (status === "done") return "已完成";
  if (status === "partial") return `部分完成 ${percent}%`;
  return "未完成";
}

function statusClass(status) {
  if (status === "done") return "is-done";
  if (status === "partial") return "is-partial";
  return "is-todo";
}

function renderPlanResult(data, fallbackDate = "") {
  const panel = document.getElementById("plan-result");
  if (!panel) return;

  const resultDate = ensureDate(data?.date || fallbackDate);
  state.planResultDate = resultDate;

  const entries = sortPlanEntries(Array.isArray(data?.plan_items) ? data.plan_items : []);
  if (!entries.length) {
    panel.innerHTML = '<p class="hint">生成后会在这里展示每日任务清单摘要。</p>';
    return;
  }

  const planDate = resultDate;
  const goalText = String(data.goal_text || "").trim();

  const itemsHtml = entries
    .map(({ item }, idx) => {
      const title = escapeHtml(item.title || `任务 ${idx + 1}`);
      const priority = escapeHtml(item.priority || "P2");
      const status = String(item.progress_status || "todo");
      const percent = normalizePercent(item.progress_percent);
      const note = String(item.progress_note || "").trim();
      const noteText = note ? escapeHtml(note) : "无";
      return `
        <li>
          <strong>${title}</strong>
          <span class="status-pill ${statusClass(status)}">${statusLabel(status, percent)}</span>
          <span class="task-priority">${priority}</span>
          <p class="task-note-inline">备注：${noteText}</p>
        </li>
      `;
    })
    .join("");

  panel.innerHTML = `
    <h3 class="plan-result-title">每日任务清单</h3>
    <p class="plan-result-meta">日期：${escapeHtml(planDate)}</p>
    <p class="plan-result-meta">目标：${escapeHtml(goalText || "未填写")}</p>
    <ol class="plan-item-list">${itemsHtml}</ol>
  `;
}

function checklistRowHtml(content = "", isDone = false, rowIndex = 0) {
  return `
    <div class="checklist-edit-row" data-role="checklist-row">
      <input
        data-role="split-select"
        data-checklist-index="${rowIndex}"
        class="split-select-input"
        type="checkbox"
        aria-label="选择此子任务用于拆分迁移"
      />
      <input data-role="sub-done" class="checklist-done-input" type="checkbox" ${isDone ? "checked" : ""} aria-label="子任务完成状态" />
      <input data-role="sub-content" type="text" value="${escapeHtml(content)}" placeholder="输入子任务内容" />
      <button data-action="remove-sub-item" type="button" class="btn-text">删除</button>
    </div>
  `;
}

function buildChecklistHtml(dateKey, items) {
  const persistedEntries = sortPlanEntries(items);
  const draftEntries = getManualDrafts(dateKey).map((item) => ({ item: normalizePlanItem(item), draftId: item._draft_id }));

  const renderCard = ({ item, persistedIndex = null, draftId = "" }, displayIdx) => {
    const isDraft = Boolean(draftId);
    const title = escapeHtml(item.title || `任务 ${displayIdx + 1}`);
    const priority = escapeHtml(item.priority || "P2");
    const estimate = item.estimate_hours ? `${item.estimate_hours} h` : "未估时";
    const doneDefinition = escapeHtml(item.done_definition || "有明确可验证产出");
    const status = item.progress_status;
    const percent = normalizePercent(item.progress_percent);
    const note = escapeHtml(item.progress_note || "");
    const hasChecklist = (item.checklist || []).length > 0;
    const progressHint = hasChecklist ? "已根据子任务自动计算进度与状态" : "当前无子任务，可手动调整进度与状态";
    const statusGroupName = isDraft ? `status-${draftId}` : `status-${persistedIndex}`;

    const checklistHtml = (item.checklist || [])
      .map((row, rowIndex) => checklistRowHtml(row.content, Boolean(row.is_done), rowIndex))
      .join("");

    const menuHtml = isDraft
      ? `
        <button data-action="discard-draft" type="button">移除草稿</button>
      `
      : `
        <button data-action="open-reschedule" type="button">更改日期</button>
        <button data-action="toggle-split-mode" type="button">拆分跳转</button>
        <button data-action="delete-task" type="button" class="is-danger">删除任务</button>
      `;

    return `
      <article class="task-card ${isDraft ? "is-draft-card" : ""}" data-item-index="${persistedIndex ?? ""}" data-draft-id="${escapeHtml(draftId)}">
        <header class="task-card-head">
          <div class="task-card-title-wrap">
            <h4>${title}</h4>
            ${isDraft ? '<span class="meta-tag">新建中</span>' : ""}
          </div>
          <div class="task-card-meta">
            <span class="status-pill ${statusClass(status)}" data-role="status-pill">${statusLabel(status, percent)}</span>
            <span class="task-priority">${priority}</span>
            <span class="task-estimate">${escapeHtml(estimate)}</span>
          </div>
        </header>

        <div class="task-edit-grid task-section">
          <label class="task-field task-field-title">
            任务标题
            <input data-role="title" type="text" value="${title}" required />
          </label>
          <label class="task-field task-field-priority">
            优先级
            <select data-role="priority">
              <option value="P0" ${item.priority === "P0" ? "selected" : ""}>P0</option>
              <option value="P1" ${item.priority === "P1" ? "selected" : ""}>P1</option>
              <option value="P2" ${item.priority === "P2" ? "selected" : ""}>P2</option>
            </select>
          </label>
          <label class="task-field task-field-estimate">
            预估时长(小时)
            <input data-role="estimate" type="number" step="0.5" min="0" value="${item.estimate_hours ?? ""}" placeholder="例如 1.5" />
          </label>
        </div>

        <label class="task-definition-field task-section">
          完成定义
          <textarea data-role="done-definition">${doneDefinition}</textarea>
        </label>

        <section class="checklist-edit-section task-section">
          <div class="checklist-edit-head">
            <strong>子任务清单（可编辑）</strong>
            <button data-action="add-sub-item" type="button" class="btn-secondary">新增条目</button>
          </div>
          <div data-role="checklist-editor" class="checklist-editor">
            ${checklistHtml}
          </div>
          <div data-role="split-actions" class="split-mode-actions is-hidden">
            <span class="hint" data-role="split-selected-hint">请选择要迁移的子任务</span>
            <button data-action="cancel-split-mode" type="button" class="btn-secondary">取消拆分</button>
            <button data-action="submit-split-move" type="button">移动至某日</button>
          </div>
        </section>

        <div class="task-status-section task-section">
          <p class="hint task-progress-hint" data-role="progress-mode-hint">${progressHint}</p>
          <div class="task-progress-row" role="radiogroup" aria-label="任务完成状态">
            <label class="status-inline-option">
              <input data-role="status" type="radio" name="${statusGroupName}" value="todo" ${status === "todo" ? "checked" : ""} ${hasChecklist ? "disabled" : ""} />
              <span>未完成</span>
            </label>
            <label class="status-inline-option">
              <input data-role="status" type="radio" name="${statusGroupName}" value="partial" ${status === "partial" ? "checked" : ""} ${hasChecklist ? "disabled" : ""} />
              <span>部分完成</span>
            </label>
            <label class="status-inline-option">
              <input data-role="status" type="radio" name="${statusGroupName}" value="done" ${status === "done" ? "checked" : ""} ${hasChecklist ? "disabled" : ""} />
              <span>已完成</span>
            </label>
          </div>

          <div class="partial-progress ${hasChecklist ? "is-locked" : ""}" data-role="partial-wrap">
            <label>
              完成度（0-100%）
              <input data-role="percent" type="range" min="0" max="100" value="${percent}" ${hasChecklist ? "disabled" : ""} />
            </label>
            <span data-role="percent-value">${percent}%</span>
          </div>
        </div>

        <label class="task-note-field task-section">
          备注
          <textarea data-role="note" placeholder="补充完成情况、阻塞原因或下一步">${note}</textarea>
        </label>

        <div class="task-actions">
          <div class="task-actions-left">
            ${isDraft ? "" : '<button data-action="regen-optimize" type="button" class="btn-secondary">AI优化描述</button>'}
            ${isDraft ? "" : '<button data-action="regen-split" type="button" class="btn-secondary">拆分更细</button>'}
          </div>
          <div class="task-actions-right">
            <div class="task-more">
              <button data-action="toggle-menu" type="button" class="btn-secondary">更多操作</button>
              <div data-role="action-menu" class="task-more-menu is-hidden">
                ${menuHtml}
              </div>
            </div>
            <button data-action="save-progress" type="button">${isDraft ? "创建任务" : "保存状态"}</button>
          </div>
        </div>
      </article>
    `;
  };

  const draftHtml = draftEntries.map((entry, index) => renderCard({ item: entry.item, draftId: entry.draftId }, index));
  const persistedHtml = persistedEntries.map(({ item, index }, displayIdx) =>
    renderCard({ item, persistedIndex: index }, displayIdx + draftEntries.length),
  );
  return [...draftHtml, ...persistedHtml].join("");
}

function closeSplitRescheduleModal() {
  const modal = document.getElementById("split-reschedule-modal");
  if (!modal) return;
  modal.classList.add("is-hidden");
  document.body.classList.remove("modal-open");
  state.splitRescheduleContext = null;
}

function openSplitRescheduleModal(context) {
  const modal = document.getElementById("split-reschedule-modal");
  const previewList = document.getElementById("split-preview-list");
  const targetDateInput = document.getElementById("split-target-date");
  const titleInput = document.getElementById("split-new-title");
  if (!modal || !previewList || !targetDateInput || !titleInput) return;

  state.splitRescheduleContext = context;
  targetDateInput.value = context.defaultTargetDate;
  titleInput.value = "";
  previewList.innerHTML = context.previewTexts.map((text) => `<li>${escapeHtml(text)}</li>`).join("");
  modal.classList.remove("is-hidden");
  document.body.classList.add("modal-open");
}

async function handleSplitReschedule({
  sourceDate,
  itemIndex,
  targetDate,
  checklistIndices,
  newTaskTitle,
  sourceItemPayload,
}) {
  if (sourceItemPayload && Number.isFinite(Number(itemIndex))) {
    await putJson("/api/plan/item", {
      date: sourceDate,
      item_index: itemIndex,
      ...sourceItemPayload,
    });
  }
  const payload = {
    source_date: sourceDate,
    item_index: itemIndex,
    target_date: targetDate,
    checklist_indices: checklistIndices,
  };
  if (String(newTaskTitle || "").trim()) {
    payload.new_task_title = String(newTaskTitle).trim();
  }
  const data = await postJson("/api/plan/item/split-migrate", payload);
  const sourceDateKey = ensureDate(data?.source_plan?.date || sourceDate);
  const targetDateKey = ensureDate(data?.target_plan?.date || targetDate);
  if (data?.source_plan) {
    state.plansByDate.set(sourceDateKey, data.source_plan);
  }
  if (data?.target_plan) {
    state.plansByDate.set(targetDateKey, data.target_plan);
  }
  renderTaskBoard(toDateKey(state.focusDate));
  showOutput(data);
  showToast(`已迁移 ${Number(data?.moved_count || checklistIndices.length)} 条子任务到 ${targetDateKey}`);
  return data;
}

function bindSplitRescheduleModal() {
  if (state.splitModalBound) return;
  state.splitModalBound = true;

  const modal = document.getElementById("split-reschedule-modal");
  const closeBtn = document.getElementById("split-modal-close-btn");
  const cancelBtn = document.getElementById("split-modal-cancel-btn");
  const form = document.getElementById("split-reschedule-form");
  const confirmBtn = document.getElementById("split-modal-confirm-btn");
  const targetDateInput = document.getElementById("split-target-date");
  const titleInput = document.getElementById("split-new-title");

  const close = () => closeSplitRescheduleModal();
  closeBtn?.addEventListener("click", close);
  cancelBtn?.addEventListener("click", close);
  modal?.addEventListener("click", (event) => {
    if (event.target === modal) {
      close();
    }
  });

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const context = state.splitRescheduleContext;
    if (!context) return;
    const targetDate = String(targetDateInput?.value || "").trim();
    if (!isValidDateKey(targetDate)) {
      showToast("请选择有效的目标日期", { error: true });
      return;
    }
    confirmBtn && (confirmBtn.disabled = true);
    confirmBtn && (confirmBtn.textContent = "迁移中...");
    try {
      await handleSplitReschedule({
        sourceDate: context.sourceDate,
        itemIndex: context.itemIndex,
        targetDate,
        checklistIndices: context.checklistIndices,
        newTaskTitle: String(titleInput?.value || "").trim(),
        sourceItemPayload: context.sourceItemPayload,
      });
      close();
    } catch (err) {
      showOutput({ error: String(err) });
      showToast(`拆分迁移失败：${String(err)}`, { error: true });
    } finally {
      confirmBtn && (confirmBtn.disabled = false);
      confirmBtn && (confirmBtn.textContent = "确认移动");
    }
  });
}

function wireTaskBoardEvents(dateKey) {
  const board = document.getElementById("task-board");
  if (!board) return;
  bindSplitRescheduleModal();

  const collectChecklistFromCard = (card) =>
    Array.from(card.querySelectorAll('[data-role="checklist-row"]'))
      .map((row) => {
        const contentInput = row.querySelector('[data-role="sub-content"]');
        const doneInput = row.querySelector('[data-role="sub-done"]');
        const content = String(contentInput?.value || "").trim();
        if (!content) return null;
        return {
          content,
          is_done: Boolean(doneInput?.checked),
        };
      })
      .filter(Boolean);

  const buildPlanItemPayloadFromCard = (card) => {
    const titleInput = card.querySelector('[data-role="title"]');
    const priorityInput = card.querySelector('[data-role="priority"]');
    const estimateInput = card.querySelector('[data-role="estimate"]');
    const doneDefinitionInput = card.querySelector('[data-role="done-definition"]');
    const noteInput = card.querySelector('[data-role="note"]');
    const percentInput = card.querySelector('[data-role="percent"]');
    const checkedStatusInput = card.querySelector('[data-role="status"]:checked');

    const title = String(titleInput?.value || "").trim();
    if (!title) {
      return {
        error: "任务标题不能为空",
        focusTarget: titleInput,
      };
    }

    const checklist = collectChecklistFromCard(card);
    let finalStatus;
    let finalPercent;
    if (checklist.length) {
      const derived = deriveStatusFromChecklist(checklist);
      finalStatus = derived.status;
      finalPercent = derived.percent;
    } else {
      finalPercent = normalizePercent(percentInput?.value || 0);
      if (finalPercent <= 0) {
        finalStatus = "todo";
        finalPercent = 0;
      } else if (finalPercent >= 100) {
        finalStatus = "done";
        finalPercent = 100;
      } else {
        finalStatus = normalizeStatus(checkedStatusInput?.value);
        if (finalStatus !== "partial") {
          finalStatus = "partial";
        }
      }
    }

    const estimateText = String(estimateInput?.value || "").trim();
    const estimateHours = estimateText ? Number(estimateText) : null;
    return {
      item: {
        title,
        priority: normalizePriority(priorityInput?.value),
        estimate_hours: Number.isFinite(estimateHours) && estimateHours > 0 ? estimateHours : null,
        done_definition: String(doneDefinitionInput?.value || "").trim(),
        checklist,
        progress_status: finalStatus,
        progress_percent: finalPercent,
        progress_note: String(noteInput?.value || "").trim(),
      },
    };
  };

  const wireCard = (card) => {
    const isDraft = Boolean(String(card.dataset.draftId || "").trim());
    const draftId = String(card.dataset.draftId || "").trim();
    const itemIndex = Number(card.dataset.itemIndex);
    const statusInputs = card.querySelectorAll('[data-role="status"]');
    const partialWrap = card.querySelector('[data-role="partial-wrap"]');
    const percentInput = card.querySelector('[data-role="percent"]');
    const percentValue = card.querySelector('[data-role="percent-value"]');
    const progressModeHint = card.querySelector('[data-role="progress-mode-hint"]');
    const statusPill = card.querySelector('[data-role="status-pill"]');
    const checklistEditor = card.querySelector('[data-role="checklist-editor"]');
    const splitActions = card.querySelector('[data-role="split-actions"]');
    const splitSelectedHint = card.querySelector('[data-role="split-selected-hint"]');
    const saveBtn = card.querySelector('[data-action="save-progress"]');
    const toggleMenuBtn = card.querySelector('[data-action="toggle-menu"]');
    const actionMenu = card.querySelector('[data-role="action-menu"]');
    const discardDraftBtn = card.querySelector('[data-action="discard-draft"]');
    const deleteTaskBtn = card.querySelector('[data-action="delete-task"]');
    const rescheduleBtn = card.querySelector('[data-action="open-reschedule"]');
    const toggleSplitModeBtn = card.querySelector('[data-action="toggle-split-mode"]');
    const cancelSplitModeBtn = card.querySelector('[data-action="cancel-split-mode"]');
    const submitSplitMoveBtn = card.querySelector('[data-action="submit-split-move"]');
    const regenOptimizeBtn = card.querySelector('[data-action="regen-optimize"]');
    const regenSplitBtn = card.querySelector('[data-action="regen-split"]');

    let autoSaveTimer = null;
    let saveBtnResetTimer = null;
    let saveInFlight = false;
    let hasPendingAutoSave = false;

    const checklistRows = () => Array.from(card.querySelectorAll('[data-role="checklist-row"]'));
    const hasChecklist = () => collectChecklistFromCard(card).length > 0;
    const currentStatus = () => {
      const checked = card.querySelector('[data-role="status"]:checked');
      return normalizeStatus(checked?.value);
    };
    const setStatus = (status) => {
      statusInputs.forEach((input) => {
        input.checked = input.value === status;
      });
    };
    const setPercent = (percent) => {
      const p = normalizePercent(percent);
      if (percentInput) percentInput.value = String(p);
      if (percentValue) percentValue.textContent = `${p}%`;
    };
    const collectChecklist = () => collectChecklistFromCard(card);
    const collectSelectedSplitRows = () => {
      const selected = [];
      let effectiveIndex = 0;
      checklistRows().forEach((row) => {
        const text = String(row.querySelector('[data-role="sub-content"]')?.value || "").trim();
        if (!text) return;
        const splitInput = row.querySelector('[data-role="split-select"]');
        if (splitInput?.checked) {
          selected.push({ index: effectiveIndex, text });
        }
        effectiveIndex += 1;
      });
      return selected;
    };

    const updateSplitSelectedHint = () => {
      if (!splitSelectedHint) return;
      const count = collectSelectedSplitRows().length;
      splitSelectedHint.textContent = count > 0 ? `已选择 ${count} 条子任务` : "请选择要迁移的子任务";
    };

    const setSplitMode = (enabled) => {
      if (isDraft) return;
      const hasSubtasks = checklistRows().length > 0;
      if (enabled && !hasSubtasks) {
        showToast("当前没有可拆分的子任务", { error: true });
        return;
      }
      card.classList.toggle("is-split-mode", Boolean(enabled));
      splitActions?.classList.toggle("is-hidden", !enabled);
      checklistRows().forEach((row) => {
        const splitInput = row.querySelector('[data-role="split-select"]');
        if (splitInput && !enabled) {
          splitInput.checked = false;
        }
      });
      updateSplitSelectedHint();
      if (toggleSplitModeBtn) {
        toggleSplitModeBtn.textContent = enabled ? "关闭拆分模式" : "拆分跳转";
      }
    };

    const syncStatusBadge = () => {
      const status = currentStatus();
      const percent = normalizePercent(percentInput?.value || 0);
      if (!statusPill) return;
      statusPill.className = `status-pill ${statusClass(status)}`;
      statusPill.textContent = statusLabel(status, percent);
    };

    const syncProgressMode = () => {
      const hasSubtasks = hasChecklist();
      if (progressModeHint) {
        progressModeHint.textContent = hasSubtasks
          ? "已根据子任务自动计算进度与状态"
          : "当前无子任务，可手动调整进度与状态";
      }
      statusInputs.forEach((input) => {
        input.disabled = hasSubtasks;
      });
      if (percentInput) {
        percentInput.disabled = hasSubtasks;
      }
      if (partialWrap) {
        partialWrap.classList.toggle("is-locked", hasSubtasks);
      }

      if (hasSubtasks) {
        const derived = deriveStatusFromChecklist(collectChecklist());
        setStatus(derived.status);
        setPercent(derived.percent);
      } else {
        const p = normalizePercent(percentInput?.value || 0);
        if (p <= 0) {
          setStatus("todo");
          setPercent(0);
        } else if (p >= 100) {
          setStatus("done");
          setPercent(100);
        } else {
          setStatus("partial");
          setPercent(p);
        }
      }
      updateSplitSelectedHint();
      syncStatusBadge();
    };

    const syncManualStatusToPercent = (status) => {
      const normalized = normalizeStatus(status);
      if (normalized === "todo") {
        setPercent(0);
      } else if (normalized === "done") {
        setPercent(100);
      } else {
        const current = normalizePercent(percentInput?.value || 0);
        if (current <= 0 || current >= 100) {
          setPercent(50);
        }
      }
      syncStatusBadge();
    };

    const setSaveButtonIdle = () => {
      if (!saveBtn) return;
      saveBtn.disabled = false;
      saveBtn.classList.remove("is-save-done", "is-save-error");
      saveBtn.textContent = isDraft ? "创建任务" : "保存状态";
    };

    const scheduleAutoSave = () => {
      if (isDraft) return;
      if (autoSaveTimer) clearTimeout(autoSaveTimer);
      autoSaveTimer = setTimeout(() => {
        void saveCardProgress({ auto: true });
      }, 1200);
    };

    const bindChecklistRow = (row) => {
      const doneInput = row.querySelector('[data-role="sub-done"]');
      const contentInput = row.querySelector('[data-role="sub-content"]');
      const removeBtn = row.querySelector('[data-action="remove-sub-item"]');
      const splitInput = row.querySelector('[data-role="split-select"]');
      doneInput?.addEventListener("change", () => {
        syncProgressMode();
        scheduleAutoSave();
      });
      contentInput?.addEventListener("input", syncProgressMode);
      splitInput?.addEventListener("change", updateSplitSelectedHint);
      removeBtn?.addEventListener("click", () => {
        row.remove();
        syncProgressMode();
      });
    };
    checklistRows().forEach((row) => bindChecklistRow(row));

    const addBtn = card.querySelector('[data-action="add-sub-item"]');
    addBtn?.addEventListener("click", () => {
      if (!checklistEditor) return;
      const rowIndex = checklistRows().length;
      checklistEditor.insertAdjacentHTML("beforeend", checklistRowHtml("", false, rowIndex));
      const rows = checklistRows();
      const row = rows[rows.length - 1];
      if (row) {
        bindChecklistRow(row);
        const input = row.querySelector('[data-role="sub-content"]');
        input?.focus();
      }
      syncProgressMode();
    });

    statusInputs.forEach((input) => {
      input.addEventListener("change", () => {
        if (hasChecklist()) {
          syncProgressMode();
          return;
        }
        syncManualStatusToPercent(normalizeStatus(input.value));
      });
    });

    percentInput?.addEventListener("input", () => {
      if (hasChecklist()) {
        syncProgressMode();
        return;
      }
      const p = normalizePercent(percentInput.value);
      setPercent(p);
      if (p <= 0) {
        setStatus("todo");
      } else if (p >= 100) {
        setStatus("done");
      } else {
        setStatus("partial");
      }
      syncStatusBadge();
    });

    const saveCardProgress = async ({ auto = false } = {}) => {
      if (saveInFlight) {
        if (auto && !isDraft) {
          hasPendingAutoSave = true;
        }
        return;
      }
      const built = buildPlanItemPayloadFromCard(card);
      if (built.error) {
        if (!auto) {
          showOutput({ error: built.error });
        }
        built.focusTarget?.focus();
        return;
      }
      saveInFlight = true;
      if (saveBtn) {
        if (saveBtnResetTimer) clearTimeout(saveBtnResetTimer);
        saveBtn.disabled = true;
        saveBtn.classList.remove("is-save-done", "is-save-error");
        saveBtn.textContent = auto ? "自动保存中..." : isDraft ? "创建中..." : "保存中...";
      }

      try {
        const data = isDraft
          ? await postJson("/api/plan/item/manual", {
              date: dateKey,
              ...built.item,
              insert_at_top: true,
            })
          : await putJson("/api/plan/item", {
              date: dateKey,
              item_index: itemIndex,
              ...built.item,
            });

        const savedDate = ensureDate(data.date || dateKey);
        state.plansByDate.set(savedDate, data);
        if (isDraft) {
          removeManualDraft(dateKey, draftId);
          renderTaskBoard(savedDate);
          showToast("任务已创建并入库");
          showOutput(data);
          return;
        }

        if (state.planResultDate === savedDate) {
          renderPlanResult(data, savedDate);
        }
        if (!auto) {
          showOutput(data);
          showToast("任务状态已保存");
        }
        if (saveBtn) {
          saveBtn.disabled = false;
          saveBtn.classList.remove("is-save-error");
          saveBtn.classList.add("is-save-done");
          saveBtn.textContent = "已保存";
          saveBtnResetTimer = setTimeout(() => {
            setSaveButtonIdle();
          }, 3000);
        }
      } catch (err) {
        if (!auto) {
          showOutput({ error: String(err) });
        }
        if (saveBtn) {
          saveBtn.disabled = false;
          saveBtn.classList.remove("is-save-done");
          saveBtn.classList.add("is-save-error");
          saveBtn.textContent = isDraft ? "创建失败" : "保存失败";
          saveBtnResetTimer = setTimeout(() => {
            setSaveButtonIdle();
          }, 3000);
        }
      } finally {
        saveInFlight = false;
        if (hasPendingAutoSave) {
          hasPendingAutoSave = false;
          scheduleAutoSave();
        }
      }
    };

    saveBtn?.addEventListener("click", async () => {
      if (autoSaveTimer) clearTimeout(autoSaveTimer);
      await saveCardProgress({ auto: false });
    });

    toggleMenuBtn?.addEventListener("click", (event) => {
      event.stopPropagation();
      actionMenu?.classList.toggle("is-hidden");
    });
    actionMenu?.addEventListener("click", () => {
      actionMenu.classList.add("is-hidden");
    });
    discardDraftBtn?.addEventListener("click", () => {
      removeManualDraft(dateKey, draftId);
      renderTaskBoard(dateKey);
      showToast("已移除草稿");
    });
    deleteTaskBtn?.addEventListener("click", async () => {
      if (!Number.isFinite(itemIndex)) return;
      if (!window.confirm("确定要删除这条任务吗？删除后无法恢复。")) return;
      try {
        const data = await deleteJson(
          `/api/plan/item?date=${encodeURIComponent(dateKey)}&item_index=${encodeURIComponent(String(itemIndex))}`,
        );
        state.plansByDate.set(dateKey, data);
        renderTaskBoard(dateKey);
        showOutput(data);
        showToast("任务已删除");
      } catch (err) {
        showOutput({ error: String(err) });
        showToast(`删除失败：${String(err)}`, { error: true });
      }
    });
    rescheduleBtn?.addEventListener("click", async () => {
      if (!Number.isFinite(itemIndex)) return;
      const defaultTarget = toDateKey(addDays(parseDateKey(dateKey), 1));
      const targetDate = String(window.prompt("请输入目标日期（YYYY-MM-DD）", defaultTarget) || "").trim();
      if (!isValidDateKey(targetDate)) {
        showToast("请输入有效日期格式（YYYY-MM-DD）", { error: true });
        return;
      }
      try {
        const data = await postJson("/api/plan/item/reschedule", {
          source_date: dateKey,
          item_index: itemIndex,
          target_date: targetDate,
        });
        const sourceDateKey = ensureDate(data?.source_plan?.date || dateKey);
        const targetDateKey = ensureDate(data?.target_plan?.date || targetDate);
        if (data?.source_plan) {
          state.plansByDate.set(sourceDateKey, data.source_plan);
        }
        if (data?.target_plan) {
          state.plansByDate.set(targetDateKey, data.target_plan);
        }
        renderTaskBoard(toDateKey(state.focusDate));
        showOutput(data);
        showToast(`任务已改期到 ${targetDateKey}`);
      } catch (err) {
        showOutput({ error: String(err) });
        showToast(`改期失败：${String(err)}`, { error: true });
      }
    });

    toggleSplitModeBtn?.addEventListener("click", () => {
      setSplitMode(!card.classList.contains("is-split-mode"));
    });
    cancelSplitModeBtn?.addEventListener("click", () => {
      setSplitMode(false);
    });
    submitSplitMoveBtn?.addEventListener("click", () => {
      if (!Number.isFinite(itemIndex)) return;
      const built = buildPlanItemPayloadFromCard(card);
      if (built.error) {
        showOutput({ error: built.error });
        built.focusTarget?.focus();
        return;
      }
      const selectedRows = collectSelectedSplitRows();
      const checklistIndices = selectedRows.map((row) => row.index);
      const previewTexts = selectedRows.map((row) => row.text);
      if (!checklistIndices.length) {
        showToast("请先勾选要拆分迁移的子任务", { error: true });
        return;
      }
      const defaultTarget = toDateKey(addDays(parseDateKey(dateKey), 1));
      openSplitRescheduleModal({
        sourceDate: dateKey,
        itemIndex,
        checklistIndices,
        previewTexts,
        defaultTargetDate: defaultTarget,
        sourceItemPayload: built.item,
      });
    });

    const bindRegenerate = (button, action) => {
      button?.addEventListener("click", async () => {
        if (!Number.isFinite(itemIndex)) return;
        const built = buildPlanItemPayloadFromCard(card);
        if (built.error) {
          showOutput({ error: built.error });
          built.focusTarget?.focus();
          return;
        }
        const originalText = button.textContent;
        button.disabled = true;
        button.textContent = "处理中...";
        try {
          await putJson("/api/plan/item", {
            date: dateKey,
            item_index: itemIndex,
            ...built.item,
          });
          const data = await postJson("/api/plan/item/regenerate", {
            date: dateKey,
            item_index: itemIndex,
            action,
          });
          const savedDate = ensureDate(data.date || dateKey);
          state.plansByDate.set(savedDate, data);
          if (state.planResultDate === savedDate) {
            renderPlanResult(data, savedDate);
          }
          renderTaskBoard(savedDate);
          showOutput(data);
          showToast(action === "split" ? "已完成任务细化" : "已优化任务描述");
        } catch (err) {
          showOutput({ error: String(err) });
          showToast(`AI处理失败：${String(err)}`, { error: true });
        } finally {
          button.disabled = false;
          button.textContent = originalText || "处理中";
        }
      });
    };
    if (!isDraft) {
      bindRegenerate(regenOptimizeBtn, "replace");
      bindRegenerate(regenSplitBtn, "split");
    }

    syncProgressMode();
    setSaveButtonIdle();
  };

  board.querySelectorAll(".task-card").forEach((card) => wireCard(card));
  if (board.dataset.menuListenerBound !== "1") {
    board.dataset.menuListenerBound = "1";
    board.addEventListener("click", (event) => {
      if (event.target.closest(".task-more")) return;
      board.querySelectorAll('[data-role="action-menu"]').forEach((menu) => menu.classList.add("is-hidden"));
    });
  }
}

function renderTaskBoard(dateKey) {
  const board = document.getElementById("task-board");
  if (!board) return;

  const plan = state.plansByDate.get(dateKey);
  const planItems = Array.isArray(plan?.plan_items) ? plan.plan_items : [];
  const draftItems = getManualDrafts(dateKey);
  if (!planItems.length && !draftItems.length) {
    board.innerHTML = '<p class="hint">这个日期还没有任务清单，点击“+ 手动添加任务”或先生成计划。</p>';
    return;
  }

  board.innerHTML = buildChecklistHtml(dateKey, planItems);
  wireTaskBoardEvents(dateKey);
}

function setTaskBoardLoading(message = "加载中...") {
  const board = document.getElementById("task-board");
  if (!board) return;
  board.innerHTML = `<p class="hint">${escapeHtml(message)}</p>`;
}

function setDayToolbarBusy(isBusy) {
  const ids = ["add-manual-task-btn", "refresh-plan-btn", "prev-day-btn", "today-btn", "next-day-btn", "focus-date-input"];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.disabled = isBusy;
    }
  });
}

async function loadPlanForDate(dateKey) {
  const board = document.getElementById("task-board");
  const requestSeq = state.latestBoardRequestSeq + 1;
  state.latestBoardRequestSeq = requestSeq;

  if (board) {
    setTaskBoardLoading("加载中...");
    setDayToolbarBusy(true);
  }

  try {
    const data = await getJson(`/api/plan/day?date=${encodeURIComponent(dateKey)}`);
    state.plansByDate.set(dateKey, data);
    if (requestSeq === state.latestBoardRequestSeq) {
      renderTaskBoard(dateKey);
    }
    return data;
  } finally {
    if (board && requestSeq === state.latestBoardRequestSeq) {
      setDayToolbarBusy(false);
    }
  }
}

function bindDayToolbar() {
  const focusDateInput = document.getElementById("focus-date-input");
  const addManualTaskBtn = document.getElementById("add-manual-task-btn");
  const refreshBtn = document.getElementById("refresh-plan-btn");
  const prevBtn = document.getElementById("prev-day-btn");
  const todayBtn = document.getElementById("today-btn");
  const nextBtn = document.getElementById("next-day-btn");

  const syncFocusInput = () => {
    if (focusDateInput) {
      focusDateInput.value = toDateKey(state.focusDate);
    }
  };

  const loadCurrent = async () => {
    const dateKey = toDateKey(state.focusDate);
    await loadPlanForDate(dateKey);
  };

  syncFocusInput();

  refreshBtn?.addEventListener("click", async () => {
    try {
      await loadCurrent();
    } catch (err) {
      showOutput({ error: String(err) });
    }
  });

  addManualTaskBtn?.addEventListener("click", async () => {
    const dateKey = toDateKey(state.focusDate);
    try {
      if (!state.plansByDate.has(dateKey)) {
        await loadCurrent();
      }
      prependManualDraft(dateKey);
      renderTaskBoard(dateKey);
      const board = document.getElementById("task-board");
      const draftCard = board?.querySelector('.task-card[data-draft-id]');
      const titleInput = draftCard?.querySelector('[data-role="title"]');
      titleInput?.focus();
      showToast("已创建空白任务卡，请填写后保存");
    } catch (err) {
      showOutput({ error: String(err) });
      showToast(`新增任务失败：${String(err)}`, { error: true });
    }
  });

  prevBtn?.addEventListener("click", async () => {
    state.focusDate = addDays(state.focusDate, -1);
    syncFocusInput();
    try {
      await loadCurrent();
    } catch (err) {
      showOutput({ error: String(err) });
    }
  });

  todayBtn?.addEventListener("click", async () => {
    state.focusDate = startOfDay(new Date());
    syncFocusInput();
    try {
      await loadCurrent();
    } catch (err) {
      showOutput({ error: String(err) });
    }
  });

  nextBtn?.addEventListener("click", async () => {
    state.focusDate = addDays(state.focusDate, 1);
    syncFocusInput();
    try {
      await loadCurrent();
    } catch (err) {
      showOutput({ error: String(err) });
    }
  });

  focusDateInput?.addEventListener("change", async () => {
    if (!focusDateInput.value) return;
    state.focusDate = parseDateKey(focusDateInput.value);
    try {
      await loadCurrent();
    } catch (err) {
      showOutput({ error: String(err) });
    }
  });
}

function bindForms() {
  const planForm = document.getElementById("plan-form");
  const reviewForm = document.getElementById("review-form");
  const planDateWarning = document.getElementById("plan-date-warning");
  const planSpanCustomFields = document.getElementById("plan-span-custom-fields");
  const planSubmitButton = planForm?.querySelector('button[type="submit"]');

  const today = toDateKey(new Date());
  if (planForm && !planForm.date.value) {
    planForm.date.value = today;
  }
  if (planForm?.date) {
    planForm.date.min = today;
  }
  if (reviewForm && !reviewForm.date.value) {
    reviewForm.date.value = today;
  }

  const bindPlanRangeFields = () => {
    if (!planForm) return;

    const syncPlanResultForSelectedDate = async () => {
      const selectedDate = ensureDate(planForm.date?.value || today);
      try {
        let data = state.plansByDate.get(selectedDate);
        if (!data) {
          data = await getJson(`/api/plan/day?date=${encodeURIComponent(selectedDate)}`);
          state.plansByDate.set(selectedDate, data);
        }
        renderPlanResult(data, selectedDate);
      } catch (err) {
        showOutput({ error: String(err) });
        const panel = document.getElementById("plan-result");
        if (panel) {
          panel.innerHTML = '<p class="hint">加载任务摘要失败，请稍后重试。</p>';
        }
      }
    };

    const toggleCustomDays = () => {
      const isCustom = String(planForm.plan_span?.value || "day") === "custom";
      if (planSpanCustomFields) {
        planSpanCustomFields.classList.toggle("is-hidden", !isCustom);
      }
      if (planForm.span_days) {
        planForm.span_days.required = isCustom;
      }
    };

    const syncPlanDateGuard = () => {
      if (!planSubmitButton) return;
      const selectedDate = ensureDate(planForm.date?.value || today);
      const isPast = isPastDateKey(selectedDate);
      planSubmitButton.disabled = isPast;
      if (planDateWarning) {
        planDateWarning.classList.toggle("is-hidden", !isPast);
      }
    };

    planForm.plan_span?.addEventListener("change", toggleCustomDays);
    planForm.date?.addEventListener("change", syncPlanDateGuard);
    planForm.date?.addEventListener("input", syncPlanDateGuard);
    planForm.date?.addEventListener("change", syncPlanResultForSelectedDate);
    toggleCustomDays();
    syncPlanDateGuard();
    syncPlanResultForSelectedDate();
  };

  const bindReviewRangeFields = () => {
    if (!reviewForm) return;
    const customFields = document.getElementById("custom-range-fields");
    if (!customFields) return;

    const toggleCustomFields = () => {
      const isCustom = String(reviewForm.range_type?.value || "day") === "custom";
      customFields.classList.toggle("is-hidden", !isCustom);
      if (reviewForm.start_date) {
        reviewForm.start_date.required = isCustom;
      }
      if (reviewForm.end_date) {
        reviewForm.end_date.required = isCustom;
      }
    };

    reviewForm.range_type?.addEventListener("change", toggleCustomFields);
    toggleCustomFields();
  };

  planForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const startDate = ensureDate(form.date.value || today);
    const spanType = String(form.plan_span?.value || "day");
    const spanDays = resolvePlanSpanDays(spanType, form.span_days?.value);
    const goalText = String(form.goal_text.value || "").trim();

    if (isPastDateKey(startDate)) {
      showOutput({ error: "过去日期不能生成任务清单，请选择今天或未来日期。" });
      return;
    }
    if (!goalText) {
      showOutput({ error: "目标不能为空。" });
      return;
    }
    if (!spanDays) {
      showOutput({ error: "请输入有效的生成天数（1-90）。" });
      return;
    }

    const startObj = parseDateKey(startDate);
    const generatedDates = [];
    let firstResponse = null;

    if (planSubmitButton) {
      planSubmitButton.disabled = true;
      planSubmitButton.textContent = `生成中 0/${spanDays}`;
    }
    try {
      for (let index = 0; index < spanDays; index += 1) {
        const targetDate = toDateKey(addDays(startObj, index));
        if (planSubmitButton) {
          planSubmitButton.textContent = `生成中 ${index + 1}/${spanDays}`;
        }
        const data = await postJson("/api/plan", {
          date: targetDate,
          goal_text: goalText,
        });
        const planDate = ensureDate(data.date || targetDate);
        generatedDates.push(planDate);
        state.plansByDate.set(planDate, data);
        if (!firstResponse && planDate === startDate) {
          firstResponse = data;
        }
      }

      const endDate = generatedDates[generatedDates.length - 1] || startDate;
      renderPlanResult(firstResponse || state.plansByDate.get(startDate), startDate);
      const focusDateKey = toDateKey(state.focusDate);
      if (generatedDates.includes(focusDateKey)) {
        renderTaskBoard(focusDateKey);
      }
      showOutput({
        status: "ok",
        message: "任务清单生成完成",
        start_date: startDate,
        end_date: endDate,
        generated_days: generatedDates.length,
        dates: generatedDates,
      });
    } catch (err) {
      showOutput({ error: String(err) });
    } finally {
      if (planSubmitButton) {
        planSubmitButton.disabled = isPastDateKey(ensureDate(form.date.value || today));
        planSubmitButton.textContent = "生成任务";
      }
    }
  });

  reviewForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const rangeType = String(form.range_type?.value || "day");
    const anchorDate = ensureDate(form.date.value || today);
    const payload = {
      range_type: rangeType,
      date: anchorDate,
      focus_text: form.focus_text?.value || "",
      done_text: form.done_text.value,
      undone_text: form.undone_text.value,
      blockers_text: form.blockers_text.value,
    };
    if (rangeType === "custom") {
      const startDate = String(form.start_date?.value || "").trim();
      const endDate = String(form.end_date?.value || "").trim();
      if (!startDate || !endDate) {
        showOutput({ error: "自定义范围需要同时填写开始日期和结束日期。" });
        return;
      }
      payload.start_date = startDate;
      payload.end_date = endDate;
    }

    try {
      const data = await postJson("/api/review", payload);
      state.reviewsByDate.set(ensureDate(data.date || anchorDate), data);
      showOutput(data);
    } catch (err) {
      showOutput({ error: String(err) });
    }
  });

  bindPlanRangeFields();
  bindReviewRangeFields();
}

if (document.getElementById("focus-date-input")) {
  bindDayToolbar();
}
if (document.getElementById("plan-form") || document.getElementById("review-form")) {
  bindForms();
}
if (document.getElementById("task-board")) {
  loadPlanForDate(toDateKey(state.focusDate))
    .catch((err) => {
      showOutput({ error: String(err) });
    });
}
