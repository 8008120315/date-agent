const state = {
  currentConversationId: "",
  conversations: [],
  contextConversationId: "",
};

const conversationListEl = document.getElementById("conversation-list");
const messageListEl = document.getElementById("message-list");
const chatFormEl = document.getElementById("chat-form");
const inputEl = document.getElementById("chat-input");
const chatTitleEl = document.getElementById("chat-title");
const chatSubtitleEl = document.getElementById("chat-subtitle");
const sendBtnEl = document.getElementById("send-btn");
const newConversationBtnEl = document.getElementById("new-conversation-btn");
const contextMenuEl = document.getElementById("conversation-menu");
const menuRenameBtnEl = document.getElementById("menu-rename-btn");
const menuDeleteBtnEl = document.getElementById("menu-delete-btn");

function shortText(text, max = 28) {
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function escapeHtml(raw) {
  return String(raw)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function safeUrl(raw) {
  try {
    const u = new URL(raw);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return u.toString();
  } catch {
    return null;
  }
}

function trimTrailingPunctuation(raw) {
  let url = raw;
  let trailing = "";
  while (url && /[),.;!?]$/.test(url)) {
    trailing = url.slice(-1) + trailing;
    url = url.slice(0, -1);
  }
  return { url, trailing };
}

function linkLabel(rawUrl) {
  const parsed = safeUrl(rawUrl);
  if (!parsed) return rawUrl;
  const u = new URL(parsed);
  const host = u.hostname.replace(/^www\./i, "");
  const path = (u.pathname || "").replace(/\/$/, "");
  if (!path) return host;
  const shortPath = path.length > 22 ? `${path.slice(0, 22)}...` : path;
  return `${host}${shortPath}`;
}

function linkifyLine(line) {
  const text = String(line || "");
  const regex = /https?:\/\/[^\s<>"'`]+/gi;
  let html = "";
  let last = 0;
  for (const match of text.matchAll(regex)) {
    const start = match.index ?? 0;
    const full = match[0];
    html += escapeHtml(text.slice(last, start));
    const { url, trailing } = trimTrailingPunctuation(full);
    const href = safeUrl(url);
    if (href) {
      html += `<a class="msg-link" href="${escapeHtml(
        href
      )}" target="_blank" rel="noopener noreferrer">${escapeHtml(linkLabel(href))}</a>`;
    } else {
      html += escapeHtml(full);
      last = start + full.length;
      continue;
    }
    html += escapeHtml(trailing);
    last = start + full.length;
  }
  html += escapeHtml(text.slice(last));
  return html || "&nbsp;";
}

function parseSourceLine(line) {
  const m = line.match(/^\s*\d+\.\s*(.*?)\s*-\s*(https?:\/\/\S+)\s*$/i);
  if (!m) return null;
  const title = (m[1] || "").trim() || "Source";
  const { url } = trimTrailingPunctuation(m[2] || "");
  const href = safeUrl(url);
  if (!href) return null;
  return { title, url: href };
}

function renderSourcesBlock(items) {
  if (!items.length) return "";
  const rows = items
    .map((it) => {
      const host = (() => {
        try {
          return new URL(it.url).hostname.replace(/^www\./i, "");
        } catch {
          return it.url;
        }
      })();
      return `
        <li>
          <a class="source-link" href="${escapeHtml(
            it.url
          )}" target="_blank" rel="noopener noreferrer">${escapeHtml(it.title)}</a>
          <span class="source-host">${escapeHtml(host)}</span>
        </li>
      `;
    })
    .join("");
  return `
    <div class="sources-box">
      <div class="sources-title">Sources</div>
      <ol>${rows}</ol>
    </div>
  `;
}

function renderMessageContent(raw) {
  const lines = String(raw || "").split(/\r?\n/);
  let html = "";
  let inSources = false;
  let sourceItems = [];

  function flushSources() {
    if (sourceItems.length) {
      html += renderSourcesBlock(sourceItems);
      sourceItems = [];
    }
  }

  for (const line of lines) {
    const trimmed = line.trim();
    if (/^sources:\s*$/i.test(trimmed)) {
      inSources = true;
      continue;
    }
    if (inSources) {
      if (!trimmed) continue;
      const src = parseSourceLine(line);
      if (src) {
        sourceItems.push(src);
        continue;
      }
      flushSources();
      inSources = false;
    }

    html += `<div class="msg-line">${linkifyLine(line)}</div>`;
  }
  flushSources();

  return html || '<div class="msg-line">&nbsp;</div>';
}

async function getJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

async function postJson(url, payload) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

function hideContextMenu() {
  contextMenuEl.classList.add("hidden");
  contextMenuEl.setAttribute("aria-hidden", "true");
  state.contextConversationId = "";
}

function showContextMenu(conversationId, x, y) {
  state.contextConversationId = conversationId;
  contextMenuEl.classList.remove("hidden");
  contextMenuEl.setAttribute("aria-hidden", "false");

  const menuRect = contextMenuEl.getBoundingClientRect();
  const maxX = window.innerWidth - menuRect.width - 8;
  const maxY = window.innerHeight - menuRect.height - 8;
  const left = Math.max(8, Math.min(x, maxX));
  const top = Math.max(8, Math.min(y, maxY));

  contextMenuEl.style.left = `${left}px`;
  contextMenuEl.style.top = `${top}px`;
}

function renderConversations() {
  if (!state.conversations.length) {
    conversationListEl.innerHTML =
      '<div class="conversation-item"><p>No sessions yet. Click "New" to start.</p></div>';
    return;
  }

  conversationListEl.innerHTML = state.conversations
    .map((c) => {
      const active = c.conversation_id === state.currentConversationId ? "active" : "";
      const preview = shortText(c.latest_message || "(empty)");
      return `
        <div class="conversation-item ${active}" data-id="${escapeHtml(c.conversation_id)}">
          <button type="button" class="conversation-open" data-id="${escapeHtml(c.conversation_id)}">
            <h3>${escapeHtml(c.conversation_id)}</h3>
            <p>${escapeHtml(preview)}</p>
          </button>
        </div>
      `;
    })
    .join("");

  conversationListEl.querySelectorAll(".conversation-open").forEach((btn) => {
    btn.addEventListener("click", () => {
      hideContextMenu();
      const id = btn.dataset.id || "";
      if (id && id !== state.currentConversationId) {
        selectConversation(id);
      }
    });

    btn.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      const id = btn.dataset.id || "";
      if (!id) return;
      showContextMenu(id, event.clientX, event.clientY);
    });
  });
}

function renderMessages(items) {
  if (!items.length) {
    messageListEl.innerHTML =
      '<div class="row ai"><div class="bubble">Start a new conversation.<span class="meta">AI</span></div></div>';
    return;
  }

  messageListEl.innerHTML = items
    .map((m) => {
      const roleClass = m.role === "assistant" ? "ai" : "user";
      const who = m.role === "assistant" ? "AI" : "You";
      const extra = m.role === "assistant" && m.model ? `${who} | ${m.model}` : who;
      return `
        <div class="row ${roleClass}">
          <div class="bubble">
            <div class="msg-content">${renderMessageContent(m.content || "")}</div>
            <span class="meta">${escapeHtml(extra)} | ${escapeHtml(m.created_at || "")}</span>
          </div>
        </div>
      `;
    })
    .join("");

  messageListEl.scrollTop = messageListEl.scrollHeight;
}

async function loadConversations(preferredId = "") {
  const data = await getJson("/api/chat/conversations?limit=200");
  state.conversations = data.items || [];

  if (!state.conversations.length) {
    if (!state.currentConversationId) {
      state.currentConversationId = `chat-${Date.now()}`;
    }
  } else if (preferredId) {
    state.currentConversationId = preferredId;
  } else if (
    !state.currentConversationId ||
    !state.conversations.some((c) => c.conversation_id === state.currentConversationId)
  ) {
    state.currentConversationId = state.conversations[0].conversation_id;
  }

  renderConversations();
  chatTitleEl.textContent = state.currentConversationId;
}

async function selectConversation(conversationId) {
  state.currentConversationId = conversationId;
  renderConversations();
  chatTitleEl.textContent = conversationId;
  chatSubtitleEl.textContent = "History";
  const data = await getJson(
    `/api/chat/history?conversation_id=${encodeURIComponent(conversationId)}&limit=200`
  );
  renderMessages(data.items || []);
}

function addLocalPendingUserMessage(text) {
  const row = `
    <div class="row user">
      <div class="bubble">
        <div class="msg-content">${renderMessageContent(text)}</div>
        <span class="meta">You | sending...</span>
      </div>
    </div>
  `;
  messageListEl.innerHTML += row;
  messageListEl.scrollTop = messageListEl.scrollHeight;
}

async function renameConversation(conversationId) {
  const input = window.prompt("New conversation id:", conversationId);
  if (!input) return;
  const newId = input.trim();
  if (!newId || newId === conversationId) return;

  try {
    await postJson("/api/chat/conversations/rename", {
      conversation_id: conversationId,
      new_conversation_id: newId,
    });
    const nextCurrent =
      state.currentConversationId === conversationId ? newId : state.currentConversationId;
    await loadConversations(nextCurrent);
    await selectConversation(nextCurrent);
  } catch (err) {
    chatSubtitleEl.textContent = `Rename failed: ${String(err)}`;
  }
}

async function deleteConversation(conversationId) {
  const ok = window.confirm(`Delete conversation "${conversationId}"?`);
  if (!ok) return;

  try {
    await postJson("/api/chat/conversations/delete", { conversation_id: conversationId });
    const remaining = state.conversations.filter((c) => c.conversation_id !== conversationId);
    const nextCurrent =
      state.currentConversationId === conversationId
        ? (remaining[0] && remaining[0].conversation_id) || `chat-${Date.now()}`
        : state.currentConversationId;
    await loadConversations(nextCurrent);
    await selectConversation(nextCurrent);
  } catch (err) {
    chatSubtitleEl.textContent = `Delete failed: ${String(err)}`;
  }
}

chatFormEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideContextMenu();
  const text = inputEl.value.trim();
  if (!text) return;

  if (!state.currentConversationId) {
    state.currentConversationId = `chat-${Date.now()}`;
  }

  sendBtnEl.disabled = true;
  addLocalPendingUserMessage(text);
  inputEl.value = "";

  try {
    const result = await postJson("/api/chat", {
      conversation_id: state.currentConversationId,
      message: text,
    });
    const resolvedConversationId = result.conversation_id || state.currentConversationId;
    state.currentConversationId = resolvedConversationId;
    await loadConversations(resolvedConversationId);
    await selectConversation(resolvedConversationId);
    chatSubtitleEl.textContent = "Updated";
  } catch (err) {
    chatSubtitleEl.textContent = `Send failed: ${String(err)}`;
  } finally {
    sendBtnEl.disabled = false;
    inputEl.focus();
  }
});

inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatFormEl.requestSubmit();
  }
});

newConversationBtnEl.addEventListener("click", () => {
  hideContextMenu();
  state.currentConversationId = `chat-${Date.now()}`;
  renderConversations();
  chatTitleEl.textContent = state.currentConversationId;
  chatSubtitleEl.textContent = "New session";
  renderMessages([]);
  inputEl.focus();
});

menuRenameBtnEl.addEventListener("click", async () => {
  const target = state.contextConversationId;
  hideContextMenu();
  if (!target) return;
  await renameConversation(target);
});

menuDeleteBtnEl.addEventListener("click", async () => {
  const target = state.contextConversationId;
  hideContextMenu();
  if (!target) return;
  await deleteConversation(target);
});

document.addEventListener("click", (event) => {
  if (!contextMenuEl.contains(event.target)) {
    hideContextMenu();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    hideContextMenu();
  }
});

messageListEl.addEventListener("scroll", hideContextMenu);
conversationListEl.addEventListener("scroll", hideContextMenu);
window.addEventListener("resize", hideContextMenu);

async function bootstrap() {
  await loadConversations();
  await selectConversation(state.currentConversationId);
}

bootstrap().catch((err) => {
  chatSubtitleEl.textContent = `Load failed: ${String(err)}`;
});
