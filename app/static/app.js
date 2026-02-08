async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

let videoOffset = 0;
let pageSize = 100;
let lastVideoCount = 0;

function fmtDate(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString().slice(0, 10);
}

async function loadChannels() {
  const channels = await api("/api/channels");
  const root = document.getElementById("channels");
  root.innerHTML = "";
  for (const ch of channels) {
    const wrap = document.createElement("div");
    const name = ch.name || ch.url;
    const text = document.createElement("span");
    text.textContent = `${name} (${ch.last_indexed_at ? `indexed ${fmtDate(ch.last_indexed_at)}` : "not indexed"})`;

    const btn = document.createElement("button");
    btn.textContent = "Index channel";
    btn.onclick = async () => {
      await api(`/api/channels/${ch.id}/index`, { method: "POST" });
      alert("Index job queued");
    };

    wrap.appendChild(btn);
    wrap.appendChild(text);
    root.appendChild(wrap);
  }
}

async function loadVideos() {
  const videos = await api(
    `/api/videos?limit=${pageSize}&offset=${videoOffset}&sort=asc&include_unavailable=false`
  );
  lastVideoCount = videos.length;
  const tbody = document.getElementById("videos");
  tbody.innerHTML = "";
  for (const v of videos) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" data-video-id="${v.video_id}" /></td>
      <td>${fmtDate(v.published_at)}</td>
      <td><a href="${v.webpage_url}" target="_blank" rel="noreferrer">${v.title}</a></td>
      <td>${v.video_id}</td>
    `;
    tbody.appendChild(tr);
  }
  const page = Math.floor(videoOffset / pageSize) + 1;
  document.getElementById("page-info").textContent = `Page ${page}`;
  document.getElementById("prev-page").disabled = videoOffset === 0;
  document.getElementById("next-page").disabled = lastVideoCount < pageSize;
}

async function loadDownloads() {
  const downloads = await api("/api/downloads");
  const tbody = document.getElementById("downloads");
  tbody.innerHTML = "";
  for (const d of downloads) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${d.video_id}</td>
      <td>${d.status}</td>
      <td>${d.filename || ""}</td>
      <td>${d.error || ""}</td>
    `;
    tbody.appendChild(tr);
  }
}

async function loadFeedInfo() {
  const info = await api("/api/feed");
  const link = document.getElementById("manual-feed-link");
  link.href = info.manual_feed_url;
  link.textContent = info.manual_feed_url;
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

document.getElementById("refresh-videos").addEventListener("click", () => loadVideos());
document.getElementById("refresh-downloads").addEventListener("click", () => loadDownloads());
document.getElementById("page-size").addEventListener("change", async (e) => {
  pageSize = Number(e.target.value) || 100;
  videoOffset = 0;
  await loadVideos();
});
document.getElementById("prev-page").addEventListener("click", async () => {
  videoOffset = Math.max(0, videoOffset - pageSize);
  await loadVideos();
});
document.getElementById("next-page").addEventListener("click", async () => {
  if (lastVideoCount < pageSize) return;
  videoOffset += pageSize;
  await loadVideos();
});
document.getElementById("regenerate-feed").addEventListener("click", async () => {
  await api("/api/feed/regenerate", { method: "POST" });
  alert("Manual feed regeneration queued");
});

document.getElementById("enqueue-selected").addEventListener("click", async () => {
  const ids = Array.from(document.querySelectorAll("#videos input[type=checkbox]:checked")).map((x) =>
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
  alert(`Queued ${res.queued} item(s)`);
  await loadDownloads();
});

async function init() {
  await loadFeedInfo();
  await loadChannels();
  await loadVideos();
  await loadDownloads();
}

init().catch((err) => {
  console.error(err);
  alert(err.message);
});
