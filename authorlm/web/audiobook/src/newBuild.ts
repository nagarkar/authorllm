// Tell an open page when a newer build is being served (author's ask
// 2026-11-21: the phone kept showing an old copy after an update). When the
// page comes back to the front, and once a minute, it reads the served page
// and looks for its own build stamp; when the stamp is gone, a banner offers
// a reload. It never reloads by itself, so nothing typed is lost.
declare const __BUILD__: string;

async function stale(): Promise<boolean> {
  try {
    const r = await fetch(location.pathname, {cache: "no-store"});
    return r.ok && !(await r.text()).includes(__BUILD__);
  } catch { return false; }
}

function banner() {
  if (document.getElementById("new-build")) return;
  const b = document.createElement("button");
  b.id = "new-build";
  b.textContent = "A new version of this page is ready. Tap to reload.";
  b.onclick = () => location.reload();
  document.body.appendChild(b);
}

export function watchForNewBuild() {
  const check = async () => { if (await stale()) banner(); };
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") void check(); });
  window.addEventListener("pageshow", () => void check());
  window.setInterval(check, 60000);
}
