async function api(path, options = {}) {
  const baseHeaders = { "Content-Type": "application/json", "x-companion-csrf": "1" };
  const mergedHeaders = { ...baseHeaders, ...(options.headers || {}) };
  const res = await fetch(path, {
    headers: mergedHeaders,
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

let channelsCache = [];
let feedInfoCache = null;
const channelVideoState = {};
const channelVideoBodies = new Map();
const openChannelPanels = new Set();
let downloadStatusByVideoId = {};
let indexStatusByChannelId = {};

function fmtDate(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString().slice(0, 10);
}

function tdWithText(value) {
  const td = document.createElement("td");
  td.textContent = value || "";
  return td;
}

function safeHttpUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return "#";
  try {
    const parsed = new URL(raw, window.location.origin);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.toString();
    }
  } catch (err) {
    // ignore parse errors and fall back to inert URL
  }
  return "#";
}

function mergedUrlForChannel(channelId) {
  if (!feedInfoCache || !feedInfoCache.merged_feed_url_template) {
    return "";
  }
  return feedInfoCache.merged_feed_url_template.replace("{channel_id}", String(channelId));
}

function formatIndexStatus(channel) {
  const status = indexStatusByChannelId[String(channel.id)];
  if (status === "pending") return "index queued";
  if (status === "running") return "indexing";
  if (status === "failed") return "index failed";
  if (channel.last_indexed_at) return `indexed ${fmtDate(channel.last_indexed_at)}`;
  return "not indexed";
}

function formatDownloadStatus(videoId) {
  const item = downloadStatusByVideoId[String(videoId || "")];
  if (!item) return "";
  return item.status || "";
}

function refreshStatusCells() {
  for (const tbody of channelVideoBodies.values()) {
    const rows = tbody.querySelectorAll("tr[data-video-id]");
    for (const row of rows) {
      const videoId = row.getAttribute("data-video-id") || "";
      const statusCell = row.querySelector(".status-cell");
      if (statusCell) {
        statusCell.textContent = formatDownloadStatus(videoId);
      }
    }
  }
}

async function refreshDownloadStatuses() {
  const downloads = await api("/api/downloads");
  const next = {};
  for (const d of downloads) {
    const videoId = String(d.video_id || "");
    if (!videoId) continue;
    next[videoId] = {
      status: String(d.status || ""),
      filename: String(d.filename || ""),
      error: String(d.error || ""),
    };
  }
  downloadStatusByVideoId = next;
  refreshStatusCells();
}

async function refreshIndexStatuses() {
  const jobs = await api("/api/jobs");
  const next = {};
  for (const job of jobs) {
    if (job.job_type !== "index_channel") continue;
    const channelId = String((job.payload && job.payload.channel_id) || "");
    if (!channelId || next[channelId]) continue;
    next[channelId] = String(job.status || "");
  }
  indexStatusByChannelId = next;
}

function getChannelState(channelId) {
  if (!channelVideoState[channelId]) {
    channelVideoState[channelId] = {
      offset: 0,
      pageSize: 50,
      lastCount: 0,
      sort: "desc",
      q: "",
      loaded: false,
    };
  }
  return channelVideoState[channelId];
}

function buildChannelVideosPanel(channel) {
  const state = getChannelState(channel.id);
  const details = document.createElement("details");
  details.className = "channel-videos";

  const summary = document.createElement("summary");
  summary.textContent = "Videos";
  details.appendChild(summary);
  if (openChannelPanels.has(channel.id)) {
    details.open = true;
  }

  const body = document.createElement("div");
  body.className = "channel-videos-body";
  details.appendChild(body);

  const toolbar = document.createElement("div");
  toolbar.className = "toolbar";

  const refreshBtn = document.createElement("button");
  refreshBtn.textContent = "Refresh";
  refreshBtn.type = "button";
  toolbar.appendChild(refreshBtn);

  const queueBtn = document.createElement("button");
  queueBtn.textContent = "Queue selected";
  queueBtn.type = "button";
  toolbar.appendChild(queueBtn);

  const sortLabel = document.createElement("label");
  sortLabel.textContent = "Sort";
  toolbar.appendChild(sortLabel);

  const sortSelect = document.createElement("select");
  sortSelect.innerHTML = `
    <option value="desc">Published newest first</option>
    <option value="asc">Published oldest first</option>
  `;
  sortSelect.value = state.sort;
  toolbar.appendChild(sortSelect);

  const searchInput = document.createElement("input");
  searchInput.placeholder = "Search title, ID, description, uploader";
  searchInput.value = state.q;
  searchInput.className = "video-search";
  toolbar.appendChild(searchInput);

  const searchBtn = document.createElement("button");
  searchBtn.textContent = "Search";
  searchBtn.type = "button";
  toolbar.appendChild(searchBtn);

  const clearBtn = document.createElement("button");
  clearBtn.textContent = "Clear";
  clearBtn.type = "button";
  toolbar.appendChild(clearBtn);

  const pageSizeLabel = document.createElement("label");
  pageSizeLabel.textContent = "Per page";
  toolbar.appendChild(pageSizeLabel);

  const pageSizeSelect = document.createElement("select");
  pageSizeSelect.innerHTML = `
    <option value="25">25</option>
    <option value="50">50</option>
    <option value="100">100</option>
    <option value="250">250</option>
  `;
  pageSizeSelect.value = String(state.pageSize);
  toolbar.appendChild(pageSizeSelect);

  const prevBtn = document.createElement("button");
  prevBtn.textContent = "Prev";
  prevBtn.type = "button";
  toolbar.appendChild(prevBtn);

  const nextBtn = document.createElement("button");
  nextBtn.textContent = "Next";
  nextBtn.type = "button";
  toolbar.appendChild(nextBtn);

  const pageInfo = document.createElement("span");
  toolbar.appendChild(pageInfo);

  body.appendChild(toolbar);

  const table = document.createElement("table");
  table.innerHTML = `
    <thead>
      <tr>
        <th></th>
        <th>Published</th>
        <th>Title</th>
        <th>Video ID</th>
        <th>Status</th>
      </tr>
    </thead>
  `;
  const tbody = document.createElement("tbody");
  table.appendChild(tbody);
  body.appendChild(table);

  async function loadChannelVideos() {
    const params = new URLSearchParams({
      channel_id: String(channel.id),
      limit: String(state.pageSize),
      offset: String(state.offset),
      sort: state.sort,
      include_unavailable: "false",
    });
    if (state.q) {
      params.set("q", state.q);
    }

    const videos = await api(`/api/videos?${params.toString()}`);
    state.lastCount = videos.length;
    state.loaded = true;

    tbody.innerHTML = "";
    if (!videos.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 5;
      td.textContent = "No indexed videos yet for this channel.";
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    for (const v of videos) {
      const tr = document.createElement("tr");
      tr.setAttribute("data-video-id", String(v.video_id || ""));
      const cbTd = document.createElement("td");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.setAttribute("data-video-id", String(v.video_id || ""));
      cbTd.appendChild(cb);

      const dateTd = tdWithText(fmtDate(v.published_at));
      const titleTd = document.createElement("td");
      const link = document.createElement("a");
      link.href = safeHttpUrl(v.webpage_url);
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = String(v.title || "");
      titleTd.appendChild(link);
      const idTd = tdWithText(String(v.video_id || ""));
      const statusTd = tdWithText(formatDownloadStatus(v.video_id));
      statusTd.className = "status-cell";

      tr.appendChild(cbTd);
      tr.appendChild(dateTd);
      tr.appendChild(titleTd);
      tr.appendChild(idTd);
      tr.appendChild(statusTd);
      tbody.appendChild(tr);
    }

    const page = Math.floor(state.offset / state.pageSize) + 1;
    pageInfo.textContent = `Page ${page}`;
    prevBtn.disabled = state.offset === 0;
    nextBtn.disabled = state.lastCount < state.pageSize;
  }

  refreshBtn.addEventListener("click", async () => {
    await loadChannelVideos();
  });

  queueBtn.addEventListener("click", async () => {
    const ids = Array.from(tbody.querySelectorAll("input[type=checkbox]:checked")).map((x) =>
      x.getAttribute("data-video-id")
    );
    if (!ids.length) {
      alert("Select at least one video");
      return;
    }
    const res = await api("/api/downloads/enqueue", {
      method: "POST",
      body: JSON.stringify({ video_ids: ids }),
    });
    const skipped = Number(res.skipped_existing || 0);
    if (skipped > 0) {
      alert(`Queued ${res.queued} item(s), skipped ${skipped} already available item(s)`);
    } else {
      alert(`Queued ${res.queued} item(s)`);
    }
    await refreshDownloadStatuses();
    refreshStatusCells();
  });

  sortSelect.addEventListener("change", async (e) => {
    state.sort = e.target.value === "asc" ? "asc" : "desc";
    state.offset = 0;
    await loadChannelVideos();
  });

  searchBtn.addEventListener("click", async () => {
    state.q = searchInput.value.trim();
    state.offset = 0;
    await loadChannelVideos();
  });

  clearBtn.addEventListener("click", async () => {
    searchInput.value = "";
    state.q = "";
    state.offset = 0;
    await loadChannelVideos();
  });

  searchInput.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    state.q = searchInput.value.trim();
    state.offset = 0;
    await loadChannelVideos();
  });

  pageSizeSelect.addEventListener("change", async (e) => {
    state.pageSize = Number(e.target.value) || 50;
    state.offset = 0;
    await loadChannelVideos();
  });

  prevBtn.addEventListener("click", async () => {
    state.offset = Math.max(0, state.offset - state.pageSize);
    await loadChannelVideos();
  });

  nextBtn.addEventListener("click", async () => {
    if (state.lastCount < state.pageSize) return;
    state.offset += state.pageSize;
    await loadChannelVideos();
  });

  details.addEventListener("toggle", async () => {
    if (details.open) openChannelPanels.add(channel.id);
    else openChannelPanels.delete(channel.id);
    if (details.open && !state.loaded) {
      await loadChannelVideos();
    }
  });

  channelVideoBodies.set(channel.id, tbody);
  if (details.open) {
    loadChannelVideos().catch((err) => {
      console.error("channel video load failed", err);
    });
  }
  return details;
}

async function loadChannels() {
  await refreshIndexStatuses();
  const channels = await api("/api/channels");
  channelsCache = channels;
  channelVideoBodies.clear();
  const root = document.getElementById("channels");
  root.innerHTML = "";

  for (const ch of channels) {
    const card = document.createElement("div");
    card.className = "channel-card";

    const header = document.createElement("div");
    header.className = "channel-header";

    const btn = document.createElement("button");
    btn.textContent = "Index channel";
    btn.type = "button";
    btn.onclick = async () => {
      await api(`/api/channels/${ch.id}/index`, { method: "POST" });
      alert("Index job queued");
    };
    header.appendChild(btn);

    const name = ch.name || ch.url;
    const meta = document.createElement("span");
    meta.textContent = `${name} (${formatIndexStatus(ch)})`;
    header.appendChild(meta);

    const mergedUrl = mergedUrlForChannel(ch.id);
    if (mergedUrl) {
      const link = document.createElement("a");
      link.href = safeHttpUrl(mergedUrl);
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "Merged feed";
      header.appendChild(link);
    }

    card.appendChild(header);

    card.appendChild(buildChannelVideosPanel(ch));

    root.appendChild(card);
  }
}

async function loadFeedInfo() {
  const info = await api("/api/feed");
  feedInfoCache = info;
  const manual = document.getElementById("manual-feed-link");
  manual.href = safeHttpUrl(info.manual_feed_url);
  manual.textContent = info.manual_feed_url;

  const mergedFeeds = await api("/api/feed/merged");
  const mergedList = document.getElementById("merged-feed-list");
  mergedList.innerHTML = "";
  if (!mergedFeeds.length) {
    mergedList.textContent = "No merged channel feeds generated yet.";
    return;
  }

  const heading = document.createElement("div");
  heading.textContent = "Merged channel feeds";
  mergedList.appendChild(heading);

  for (const feed of mergedFeeds) {
    const row = document.createElement("div");
    const label = document.createElement("span");
    label.textContent = `${feed.channel_name}: `;
    const link = document.createElement("a");
    link.href = safeHttpUrl(feed.url);
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = feed.url;
    row.appendChild(label);
    row.appendChild(link);
    mergedList.appendChild(row);
  }
}

document.getElementById("channel-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = document.getElementById("channel-url").value.trim();
  const name = document.getElementById("channel-name").value.trim();
  if (!url) return;

  await api("/api/channels", {
    method: "POST",
    body: JSON.stringify({ url, name: name || null }),
  });
  document.getElementById("channel-url").value = "";
  document.getElementById("channel-name").value = "";
  await loadChannels();
});

document.getElementById("sync-from-podsync").addEventListener("click", async () => {
  const result = await api("/api/channels/sync_from_podsync", { method: "POST" });
  alert(`Imported ${result.added} channel(s) from Podsync config`);
  await loadChannels();
});

document.getElementById("index-all-channels").addEventListener("click", async () => {
  if (!channelsCache.length) {
    await loadChannels();
  }
  if (!channelsCache.length) {
    alert("No channels available to index");
    return;
  }

  let queued = 0;
  for (const ch of channelsCache) {
    await api(`/api/channels/${ch.id}/index`, { method: "POST" });
    queued += 1;
  }
  alert(`Queued index jobs for ${queued} channel(s)`);
  await loadChannels();
});

document.getElementById("regenerate-feed").addEventListener("click", async () => {
  await api("/api/feed/regenerate", { method: "POST" });
  alert("Feed regeneration queued");
  await loadFeedInfo();
  await loadChannels();
});

async function init() {
  await refreshDownloadStatuses();
  await loadFeedInfo();
  await loadChannels();
  window.setInterval(async () => {
    try {
      await refreshDownloadStatuses();
      await loadChannels();
    } catch (err) {
      console.error("background refresh failed", err);
    }
  }, 7000);
}

init().catch((err) => {
  console.error(err);
  alert(err.message);
});
