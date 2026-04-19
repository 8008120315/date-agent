function $(id) {
  return document.getElementById(id)
}

const reviewState = {
  records: [],
  filters: {
    q: "",
    start_date: "",
    end_date: "",
    limit: 50,
    offset: 0,
  },
}

const reviewRecordCache = new Map()

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;")
}

function formatDate(value) {
  const text = String(value || "").trim()
  if (!text) return ""
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text
  const dt = new Date(text.replace(" ", "T"))
  if (Number.isNaN(dt.getTime())) return text
  const y = dt.getFullYear()
  const m = String(dt.getMonth() + 1).padStart(2, "0")
  const d = String(dt.getDate()).padStart(2, "0")
  return `${y}-${m}-${d}`
}

function formatDateTime(value) {
  const text = String(value || "").trim()
  if (!text) return "-"
  const dt = new Date(text.replace(" ", "T"))
  if (Number.isNaN(dt.getTime())) return text
  const y = dt.getFullYear()
  const m = String(dt.getMonth() + 1).padStart(2, "0")
  const d = String(dt.getDate()).padStart(2, "0")
  const hh = String(dt.getHours()).padStart(2, "0")
  const mm = String(dt.getMinutes()).padStart(2, "0")
  return `${y}-${m}-${d} ${hh}:${mm}`
}

function parseInlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
}

function renderMarkdownFallback(markdownText) {
  const lines = String(markdownText || "").split(/\r?\n/)
  const html = []
  let listType = ""

  const closeList = () => {
    if (listType === "ul") html.push("</ul>")
    if (listType === "ol") html.push("</ol>")
    listType = ""
  }

  for (const rawLine of lines) {
    const line = rawLine.trim()
    if (!line) {
      closeList()
      continue
    }
    if (line.startsWith("### ")) {
      closeList()
      html.push(`<h4>${parseInlineMarkdown(line.slice(4))}</h4>`)
      continue
    }
    if (line.startsWith("## ")) {
      closeList()
      html.push(`<h3>${parseInlineMarkdown(line.slice(3))}</h3>`)
      continue
    }
    if (line.startsWith("# ")) {
      closeList()
      html.push(`<h2>${parseInlineMarkdown(line.slice(2))}</h2>`)
      continue
    }

    const ul = line.match(/^- (.+)$/)
    if (ul) {
      if (listType !== "ul") {
        closeList()
        html.push("<ul>")
        listType = "ul"
      }
      html.push(`<li>${parseInlineMarkdown(ul[1])}</li>`)
      continue
    }

    const ol = line.match(/^\d+\.\s+(.+)$/)
    if (ol) {
      if (listType !== "ol") {
        closeList()
        html.push("<ol>")
        listType = "ol"
      }
      html.push(`<li>${parseInlineMarkdown(ol[1])}</li>`)
      continue
    }

    closeList()
    html.push(`<p>${parseInlineMarkdown(line)}</p>`)
  }

  closeList()
  return html.join("")
}

function normalizePlainTextToMarkdown(text) {
  const raw = String(text || "").trim()
  if (!raw) return ""
  const maybeMarkdown = /(^|\n)\s*(#{1,4}\s+|- |\d+\.\s+)/.test(raw)
  if (maybeMarkdown) return raw

  const lines = raw.split(/\r?\n/).map((line) => line.trim())
  const out = []
  let hasOverview = false

  for (const line of lines) {
    if (!line) continue
    if (line === "任务完成情况：") {
      out.push("## 任务完成情况")
      continue
    }
    if (line === "用户补充输入：") {
      out.push("## 用户补充输入")
      continue
    }
    if (line.startsWith("复盘范围：") || line.startsWith("开始日期：") || line.startsWith("结束日期：") || line.startsWith("复盘关注点：")) {
      if (!hasOverview) {
        out.push("## 复盘概览")
        hasOverview = true
      }
      out.push(`- ${line}`)
      continue
    }
    if (/^\[\d{4}-\d{2}-\d{2}\]/.test(line)) {
      out.push(`### ${line}`)
      continue
    }
    if (line.startsWith("- ")) {
      out.push(line)
      continue
    }
    out.push(line)
  }
  return out.join("\n")
}

function renderReviewContentToHtml(rawContent) {
  const markdown = normalizePlainTextToMarkdown(rawContent)
  if (!markdown) return "<p class=\"hint\">无内容</p>"

  if (window.marked && typeof window.marked.parse === "function") {
    const safeMarkdown = markdown.replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    return window.marked.parse(safeMarkdown, { mangle: false, headerIds: false })
  }
  return renderMarkdownFallback(markdown)
}

function cleanSummary(summary) {
  return String(summary || "")
    .replace(/^\[[^\]]+\]\s*复盘[:：]\s*/u, "")
    .trim()
}

function buildMainTitle(item, metadata) {
  const reviewDate = formatDate(item.date) || formatDate(item.created_at)
  const periodText = String(metadata?.period_label || "").trim()
  let periodLabel = "每日"
  if (periodText.includes("最近三天")) periodLabel = "最近三天"
  if (periodText.includes("上周")) periodLabel = "上周"
  if (periodText.includes("自定义")) periodLabel = "自定义"
  if (!reviewDate) return `${periodLabel}复盘摘要`
  return `${reviewDate} ${periodLabel}复盘摘要`
}

function getCopyText(item) {
  const summary = cleanSummary(item.summary)
  const content = String(item.content || "").trim()
  if (!content) return summary
  return summary ? `${summary}\n\n${content}` : content
}

function buildTag(label) {
  return `<span class="meta-tag">${escapeHtml(label)}</span>`
}

function readErrorMessage(response) {
  const fallback = `HTTP ${response.status}`
  return response
    .text()
    .then((text) => {
      if (!text) return fallback
      try {
        const parsed = JSON.parse(text)
        if (parsed && typeof parsed === "object" && typeof parsed.detail === "string" && parsed.detail.trim()) {
          return parsed.detail.trim()
        }
      } catch {
        // ignore
      }
      return text
    })
    .catch(() => fallback)
}

function setStatus(message, isError = false) {
  const status = $("review-records-status")
  if (!status) return
  status.textContent = message
  status.classList.toggle("is-error", isError)
}

function showToast(message, type = "success") {
  const container = $("toast-container")
  if (!container) return
  const toast = document.createElement("div")
  toast.className = `toast-item is-${type}`
  toast.textContent = message
  container.appendChild(toast)
  setTimeout(() => {
    toast.classList.add("is-leaving")
    setTimeout(() => toast.remove(), 220)
  }, 2400)
}

function getRecordById(recordId) {
  const id = Number(recordId)
  if (!Number.isFinite(id)) return null
  return reviewState.records.find((item) => Number(item.id) === id) || null
}

function buildCard(item) {
  const metadata = item && typeof item.metadata === "object" ? item.metadata : {}
  const periodLabel = String(metadata.period_label || "").trim() || "未标注范围"
  const sourceModel = String(metadata.source_model || "").trim() || "未知模型"
  const createdAt = formatDateTime(item.created_at)
  const reviewDate = formatDate(item.date) || "-"
  const mainTitle = buildMainTitle(item, metadata)
  const summary = cleanSummary(item.summary) || "暂无摘要"
  const rendered = renderReviewContentToHtml(item.content)
  const copyText = getCopyText(item)
  const recordId = String(item.id)
  reviewRecordCache.set(recordId, copyText)
  const coveredDates = Array.isArray(metadata.covered_dates) ? metadata.covered_dates : []

  const coveredTag = coveredDates.length ? `覆盖${coveredDates.length}天` : "覆盖天数未知"
  const metaTags = [
    `记录日期 ${reviewDate}`,
    `创建 ${createdAt}`,
    periodLabel,
    `模型 ${sourceModel}`,
    coveredTag,
  ]

  return `
    <article class="review-record-card" data-id="${item.id}">
      <header class="review-record-head">
        <div class="review-record-main">
          <h3 class="review-record-title">${escapeHtml(mainTitle)}</h3>
          <p class="review-record-subtitle">${escapeHtml(summary)}</p>
          <div class="review-meta-tags">
            ${metaTags.map((tag) => buildTag(tag)).join("")}
          </div>
        </div>
        <div class="review-action-group">
          <button
            type="button"
            class="review-action-btn"
            data-action="edit"
            data-record-id="${escapeHtml(recordId)}"
            title="编辑"
          >
            编辑
          </button>
          <button
            type="button"
            class="review-action-btn"
            data-action="copy"
            data-record-id="${escapeHtml(recordId)}"
            title="复制"
          >
            复制
          </button>
          <button
            type="button"
            class="review-action-btn is-danger"
            data-action="delete"
            data-record-id="${escapeHtml(recordId)}"
            title="删除"
          >
            删除
          </button>
        </div>
      </header>
      <details class="review-record-detail">
        <summary>
          <span class="summary-arrow" aria-hidden="true">▸</span>
          <span>展开查看完整复盘</span>
        </summary>
        <div class="review-record-collapse">
          <div class="review-record-collapse-inner">
            <div class="review-record-content markdown-body">${rendered}</div>
          </div>
        </div>
      </details>
    </article>
  `
}

function applyTokenTags(root) {
  if (!root) return
  let html = root.innerHTML
  html = html.replace(/\b(P0|P1|P2)\b/g, (full, level) => `<span class="token-tag priority-${level.toLowerCase()}">${level}</span>`)
  html = html.replace(/部分完成(?:\(\d+%\))?/g, (text) => `<span class="token-tag status-partial">${text}</span>`)
  html = html.replace(/未完成/g, "<span class=\"token-tag status-todo\">未完成</span>")
  html = html.replace(/已完成/g, "<span class=\"token-tag status-done\">已完成</span>")
  html = html.replace(/阻塞/g, "<span class=\"token-tag status-blocked\">阻塞</span>")
  html = html.replace(/\[(x|X)\]/g, "<span class=\"token-check is-done\">✓</span>")
  html = html.replace(/\[\s*\]/g, "<span class=\"token-check\">□</span>")
  root.innerHTML = html
}

function groupMarkdownBlocks(root) {
  if (!root) return
  const body = root.querySelector(".review-record-content")
  if (!body) return
  const children = Array.from(body.children)
  if (!children.length) return

  const fragment = document.createDocumentFragment()
  let section = null
  let sectionCount = 0

  for (const node of children) {
    const isHeading = /^H[234]$/.test(node.tagName)
    if (isHeading) {
      section = document.createElement("section")
      section.className = "review-content-block"

      const title = document.createElement("h4")
      title.className = "review-content-title"
      title.innerHTML = node.innerHTML
      section.appendChild(title)
      fragment.appendChild(section)
      sectionCount += 1
      continue
    }

    if (!section) {
      section = document.createElement("section")
      section.className = "review-content-block"
      const title = document.createElement("h4")
      title.className = "review-content-title"
      title.textContent = "复盘内容"
      section.appendChild(title)
      fragment.appendChild(section)
      sectionCount += 1
    }
    section.appendChild(node)
  }

  if (sectionCount === 0) return
  body.innerHTML = ""
  body.appendChild(fragment)
}

function decorateRenderedContent() {
  const records = document.querySelectorAll(".review-record-card")
  records.forEach((card) => {
    groupMarkdownBlocks(card)
    const content = card.querySelector(".review-record-content")
    applyTokenTags(content)
  })
}

function renderRecordList() {
  const listEl = $("review-records-list")
  if (!listEl) return
  reviewRecordCache.clear()
  if (!reviewState.records.length) {
    listEl.innerHTML = `<p class="hint">暂无符合条件的复盘记录。</p>`
    return
  }
  listEl.innerHTML = reviewState.records.map((item) => buildCard(item)).join("")
  decorateRenderedContent()
}

async function handleSearch(params = {}) {
  reviewState.filters = {
    ...reviewState.filters,
    ...params,
  }
  const listEl = $("review-records-list")
  const query = new URLSearchParams({
    limit: String(reviewState.filters.limit),
    offset: String(reviewState.filters.offset),
  })
  if (reviewState.filters.q) query.set("q", reviewState.filters.q)
  if (reviewState.filters.start_date) query.set("start_date", reviewState.filters.start_date)
  if (reviewState.filters.end_date) query.set("end_date", reviewState.filters.end_date)

  setStatus("正在加载复盘记录...")
  try {
    const response = await fetch(`/api/review/records?${query.toString()}`)
    if (!response.ok) {
      throw new Error(await readErrorMessage(response))
    }
    const data = await response.json()
    const items = Array.isArray(data?.items) ? data.items : []
    reviewState.records = items
    renderRecordList()
    setStatus(items.length ? `已加载 ${items.length} 条复盘记录` : "暂无数据")
  } catch (err) {
    reviewState.records = []
    if (listEl) {
      listEl.innerHTML = `<p class="hint">加载失败，请稍后重试。</p>`
    }
    setStatus(`加载失败：${String(err)}`, true)
  }
}

async function loadRecords() {
  await handleSearch({})
}

function openEditModal(recordId) {
  const record = getRecordById(recordId)
  if (!record) {
    showToast("记录不存在，无法编辑", "error")
    return
  }
  const modal = $("review-edit-modal")
  const idInput = $("review-edit-id")
  const summaryInput = $("review-edit-summary")
  const contentInput = $("review-edit-content")
  if (!modal || !idInput || !summaryInput || !contentInput) return

  idInput.value = String(record.id)
  summaryInput.value = String(record.summary || "").trim()
  contentInput.value = String(record.content || "").trim()
  modal.classList.remove("is-hidden")
  document.body.classList.add("modal-open")
  summaryInput.focus()
}

function closeEditModal() {
  const modal = $("review-edit-modal")
  if (!modal) return
  modal.classList.add("is-hidden")
  document.body.classList.remove("modal-open")
}

async function handleUpdate(id, newData) {
  const response = await fetch(`/api/review/records/${encodeURIComponent(String(id))}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(newData),
  })
  if (!response.ok) {
    throw new Error(await readErrorMessage(response))
  }
  const data = await response.json()
  return data?.item || null
}

async function handleDelete(id) {
  const response = await fetch(`/api/review/records/${encodeURIComponent(String(id))}`, {
    method: "DELETE",
  })
  if (!response.ok) {
    throw new Error(await readErrorMessage(response))
  }
}

function bindCardActions() {
  const listEl = $("review-records-list")
  if (!listEl) return

  listEl.addEventListener("click", async (event) => {
    const target = event.target
    if (!(target instanceof HTMLElement)) return
    const button = target.closest("[data-action]")
    if (!(button instanceof HTMLElement)) return

    const action = button.getAttribute("data-action") || ""
    const recordId = button.getAttribute("data-record-id") || ""
    if (!recordId) return

    if (action === "copy") {
      const text = reviewRecordCache.get(recordId) || ""
      if (!text.trim()) return
      try {
        await navigator.clipboard.writeText(text)
        showToast("已复制复盘内容")
      } catch {
        showToast("复制失败，请检查浏览器剪贴板权限", "error")
      }
      return
    }

    if (action === "edit") {
      openEditModal(recordId)
      return
    }

    if (action === "delete") {
      const ok = window.confirm("确定要删除这条复盘记录吗？此操作不可恢复。")
      if (!ok) return
      try {
        await handleDelete(recordId)
        reviewState.records = reviewState.records.filter((item) => String(item.id) !== String(recordId))
        renderRecordList()
        setStatus(`已删除记录 #${recordId}`)
        showToast("删除成功")
      } catch (err) {
        showToast(`删除失败：${String(err)}`, "error")
      }
    }
  })
}

function bindEditModal() {
  const modal = $("review-edit-modal")
  const closeBtn = $("close-review-modal-btn")
  const cancelBtn = $("cancel-review-edit-btn")
  const form = $("review-edit-form")
  const saveBtn = $("save-review-edit-btn")
  const summaryInput = $("review-edit-summary")
  const contentInput = $("review-edit-content")
  const idInput = $("review-edit-id")

  closeBtn?.addEventListener("click", closeEditModal)
  cancelBtn?.addEventListener("click", closeEditModal)
  modal?.addEventListener("click", (event) => {
    if (event.target === modal) {
      closeEditModal()
    }
  })

  form?.addEventListener("submit", async (event) => {
    event.preventDefault()
    const id = Number(idInput?.value || 0)
    const summary = String(summaryInput?.value || "").trim()
    const content = String(contentInput?.value || "").trim()
    if (!id || !summary || !content) {
      showToast("请完整填写摘要和复盘内容", "error")
      return
    }

    if (saveBtn) {
      saveBtn.disabled = true
      saveBtn.textContent = "保存中..."
    }
    try {
      const updated = await handleUpdate(id, { summary, content })
      if (!updated) {
        throw new Error("更新失败，未返回记录")
      }
      reviewState.records = reviewState.records.map((item) => (Number(item.id) === id ? updated : item))
      renderRecordList()
      closeEditModal()
      showToast("保存成功")
      setStatus(`已更新记录 #${id}`)
    } catch (err) {
      showToast(`保存失败：${String(err)}`, "error")
    } finally {
      if (saveBtn) {
        saveBtn.disabled = false
        saveBtn.textContent = "保存修改"
      }
    }
  })

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal && !modal.classList.contains("is-hidden")) {
      closeEditModal()
    }
  })
}

function bindFilterControls() {
  const refreshBtn = $("refresh-review-records-btn")
  const searchBtn = $("search-review-records-btn")
  const resetBtn = $("reset-review-filters-btn")
  const limitEl = $("review-limit-select")
  const searchInput = $("review-search-input")
  const startInput = $("review-start-date")
  const endInput = $("review-end-date")

  const doSearch = async () => {
    const q = String(searchInput?.value || "").trim()
    const start_date = String(startInput?.value || "").trim()
    const end_date = String(endInput?.value || "").trim()
    const limit = Number(limitEl?.value || reviewState.filters.limit)

    if (start_date && end_date && start_date > end_date) {
      showToast("开始日期不能晚于结束日期", "error")
      return
    }
    await handleSearch({
      q,
      start_date,
      end_date,
      limit,
      offset: 0,
    })
  }

  refreshBtn?.addEventListener("click", () => {
    doSearch()
  })
  searchBtn?.addEventListener("click", () => {
    doSearch()
  })
  resetBtn?.addEventListener("click", async () => {
    if (searchInput) searchInput.value = ""
    if (startInput) startInput.value = ""
    if (endInput) endInput.value = ""
    if (limitEl) limitEl.value = "50"
    await handleSearch({
      q: "",
      start_date: "",
      end_date: "",
      limit: 50,
      offset: 0,
    })
  })
  limitEl?.addEventListener("change", () => {
    doSearch()
  })
  searchInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault()
      doSearch()
    }
  })
}

function bindReviewRecordsPage() {
  bindFilterControls()
  bindCardActions()
  bindEditModal()
  loadRecords()
}

bindReviewRecordsPage()
