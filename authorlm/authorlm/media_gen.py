"""Generated clips: plan, start, poll, take (docs/video-clips-design.md
§8b, §8c, §8f, §8j; slice 3).

A generated clip ([Clip] with a generation spec, no `youtube:`/`use:`) is
made by Veo through the Gemini API, over plain REST as `llm._gemini_image`
is. The road is the design's:

- `plan` composes everything a generation would send and what it costs,
  and runs the gate. It spends nothing: it IS the dry run.
- `start` sends the request and records a job in `_clips/jobs/<id>.json`;
  generation takes seconds to minutes, so it never waits.
- `poll` checks a job and, once Veo is done, downloads the take to
  `_clips/<key>-<deschash8>-<lawhash4>-NN.mp4`. The first take of a clip
  is chosen automatically (recorded in `_clips/chosen.json`, the store of
  record) so a caller always gets a path; `pick` moves the choice,
  `attach` brings in an outside file as `iNN`.

The deschash fingerprints the spec and the fields that shape the picture;
the lawhash the effective `clip` law. A take whose hashes differ from the
clip's current ones is stale (`takes`). Nothing regenerates by itself.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import media

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
JOBS_SUBDIR = "jobs"
SETTINGS_FILE = "settings.toml"

# Gemini API list prices, USD per output second (2026-10-17; verify on
# Google's pricing page before relying on them).
MODELS = {
    "veo-3.1-generate-preview": {"720p": 0.40, "1080p": 0.40, "4k": 0.60},
    "veo-3.1-fast-generate-preview": {"720p": 0.10, "1080p": 0.12, "4k": 0.30},
    "veo-3.1-lite-generate-preview": {"720p": 0.05, "1080p": 0.08},
}
DEFAULTS = {"model": "veo-3.1-fast-generate-preview", "resolution": "720p",
            "aspect_ratio": "16:9", "max_call_usd": 2.0, "max_day_usd": 10.0}
DURATIONS = (4, 6, 8)
TAKE = re.compile(r"^(?P<key>.+)-(?P<desc>[0-9a-f]{8})-(?P<law>[0-9a-f]{4})-"
                  r"(?P<n>i?\d{2})\.(?P<ext>mp4|mov|m4v|webm)$")


# ------------------------------------------------------------------ settings

def settings(root: Path) -> dict:
    """The manuscript's clip pin (`_clips/settings.toml`) over DEFAULTS.
    A style is model-bound, so the model is pinned per manuscript, as
    illustrations' is."""
    import tomllib

    out = dict(DEFAULTS)
    path = Path(root) / media.CLIPS_DIR / SETTINGS_FILE
    if path.exists():
        out.update(tomllib.loads(path.read_text(encoding="utf-8")))
    return out


def clip_law(db, manuscript_id: str, file: str) -> str:
    """The effective `clip` style law for a file, as the exact text the
    prompt carries; '' when none is ratified yet."""
    from .styles import effective_style

    elements = [e for e in effective_style(db, manuscript_id, file)
                if e["aspect"] == "clip"]
    if not elements:
        return ""
    lines = ["CLIP STYLE (ratified by the author — follow strictly):"]
    lines += [f"- {e['statement']}" + (f" ({e['notes']})" if e.get("notes")
                                       else "") for e in elements]
    return "\n".join(lines)


def _hash(text: str, n: int) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n] if text \
        else "0" * n


def desc_hash(spec: str, fields: dict) -> str:
    """What shapes the picture: the spec and the visual fields."""
    shaping = {k: fields[k] for k in ("start", "end", "cast", "prior", "look",
                                      "text") if k in fields}
    return _hash(" ".join(spec.split()) + json.dumps(shaping, sort_keys=True), 8)


# ------------------------------------------------------------------ takes

def takes(root: Path, key: str, want_desc: str | None = None,
          want_law: str | None = None) -> list[dict]:
    """Every take of a clip on disk, in take order, each with its
    staleness against the current hashes when given."""
    directory = Path(root) / media.CLIPS_DIR
    out = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.iterdir()):
        m = TAKE.match(path.name)
        if not m or m.group("key") != key:
            continue
        stale = []
        if want_desc and m.group("desc") != want_desc:
            stale.append("stale-desc")
        if want_law and m.group("law") != want_law:
            stale.append("stale-law")
        out.append({"name": path.name, "n": m.group("n"),
                    "path": f"{media.CLIPS_DIR}/{path.name}", "stale": stale})
    return sorted(out, key=lambda t: (t["n"].lstrip("i").zfill(3), t["n"]))


def _next_n(root: Path, key: str, imported: bool = False) -> str:
    numbers = [int(t["n"].lstrip("i")) for t in takes(root, key)]
    n = (max(numbers) + 1) if numbers else 1
    return f"{'i' if imported else ''}{n:02d}"


CHOSEN_FILE = "chosen.json"


def _chosen_map(root: Path) -> dict:
    path = Path(root) / media.CLIPS_DIR / CHOSEN_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _set_chosen(root: Path, key: str, name: str) -> None:
    path = Path(root) / media.CLIPS_DIR / CHOSEN_FILE
    data = _chosen_map(root)
    data[key] = name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")


def chosen(root: Path, key: str) -> str | None:
    """The chosen take's file name, from `_clips/chosen.json` — the store
    of record. Not an embed line under the tag: the Doc never carries one,
    so a pull would lose it."""
    name = _chosen_map(root).get(key)
    if name and (Path(root) / media.CLIPS_DIR / name).exists():
        return name
    return None


def pick(root: Path, key: str, n: str) -> dict:
    """Register the chosen take (the author chose; podlm or chat records
    it)."""
    clip = media.find_clip(root, key)
    ckey = clip["ref"][:-3]
    match = [t for t in takes(root, ckey)
             if t["n"] == str(n) or t["n"] == str(n).zfill(2)]
    if not match:
        raise LookupError(f"{key} has no take {n} — 'media list --key {key}' "
                          "shows its takes")
    _set_chosen(root, ckey, match[0]["name"])
    return {"key": ckey, "chosen": match[0]["path"]}


def attach(root: Path, key: str, source: Path, desc: str = "0" * 8,
           law: str = "0000") -> dict:
    """Bring an outside file in as take `iNN` of a clip. Returns its path."""
    import shutil

    clip = media.find_clip(root, key)
    source = Path(source)
    ext = source.suffix.lower().lstrip(".")
    if ext not in ("mp4", "mov", "m4v", "webm"):
        raise ValueError(f"{source.name} is not a video (mp4, mov, m4v, webm)")
    name = (f"{clip['ref'][:-3]}-{desc}-{law}-"
            f"{_next_n(root, clip['ref'][:-3], imported=True)}.{ext}")
    dest = Path(root) / media.CLIPS_DIR / name
    shutil.copyfile(source, dest)
    if not chosen(root, clip["ref"][:-3]):
        _set_chosen(root, clip["ref"][:-3], name)
    return {"key": clip["ref"][:-3], "path": f"{media.CLIPS_DIR}/{name}"}


# ------------------------------------------------------------------ plan

def _image_ref(root: Path, value: str, known_cast: dict) -> Path | None:
    """A frame or reference value → an image file: a path, an illustration
    key's chosen image, or a cast id's plate."""
    from .illus import ILLUS_DIR

    if media._is_path(value):
        return Path(root) / value
    plate = known_cast.get(value)
    if plate:
        return Path(root) / ILLUS_DIR / plate
    return None


def _duration(entry: dict, forced_eight: bool) -> int:
    if forced_eight:
        return 8
    want = entry.get("estimated_seconds") or 8
    return next((d for d in DURATIONS if d >= want), 8)


def compose_prompt(spec: str, law: str, fields: dict) -> str:
    """The text Veo receives: the generation spec's sections, the clip law,
    and the exact on-screen words (or none)."""
    sections = media.parse_sections(spec, media.SPEC_LABELS)
    body = "\n".join(f"{label.upper()}: {sections[label]}"
                     for label in media.SPEC_LABELS if label in sections) \
        or spec
    parts = [body]
    if fields.get("text"):
        parts.append(f'ON-SCREEN TEXT, exactly: "{fields["text"]}"')
    else:
        parts.append("No on-screen text, captions or logos.")
    if law:
        parts.append(law)
    return "\n\n".join(parts)


def day_spend(root: Path, today: str | None = None) -> float:
    """Estimated USD of jobs started today (local date), from the job files."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    total = 0.0
    for path in sorted((Path(root) / media.CLIPS_DIR / JOBS_SUBDIR).glob("*.json")):
        job = json.loads(path.read_text(encoding="utf-8"))
        if job.get("started_local", "").startswith(today):
            total += float(job.get("est_usd") or 0)
    return total


def plan(db, manuscript: dict, key: str, model: str | None = None) -> dict:
    """Everything a generation would send and cost, plus the gate. Spends
    nothing — this is `--dry-run`. Returns {key, file, model, resolution,
    aspect_ratio, duration, est_usd, prompt, first_frame, last_frame,
    references, desc, law, reasons, needs_confirm, fresh_takes}."""
    root = Path(manuscript["path"])
    listing = media.list_media(root, None, key)["media"]
    if not listing or listing[0]["kind"] != "clip":
        raise LookupError(f"no clip with key {key} — 'media list' shows keys")
    entry = listing[0]
    cfg = settings(root)
    model = model or cfg["model"]
    reasons: list[str] = []
    if entry["spec_kind"] != "generation_spec":
        reasons.append(f"{key} is a {entry['spec_kind']} clip — only a clip "
                       "with a generation spec is generated")
    reasons += entry["errors"] + entry.get("draft_reasons", [])
    if model not in MODELS:
        reasons.append(f"unknown model {model} (one of: {', '.join(MODELS)})")
    fields = entry["fields"]
    spec_path = root / entry["spec_path"] if entry.get("spec_path") else None
    spec = (spec_path.read_text(encoding="utf-8").strip()
            if spec_path and spec_path.exists() else entry["description"])
    law = clip_law(db, manuscript["id"], entry["file"])
    from .illus import cast_index

    index, _ = cast_index(root)
    known = {cid: c.get("plate") for cid, c in index.items()}
    first = _image_ref(root, fields["start"], known) if "start" in fields else None
    last = _image_ref(root, fields["end"], known) if "end" in fields else None
    refs = [p for p in (_image_ref(root, c, known) for c in fields.get("cast", []))
            if p]
    if refs and model.startswith("veo-3.1-lite"):
        reasons.append("reference images (cast:) need Veo 3.1 or Fast, not Lite")
    resolution = cfg["resolution"]
    duration = _duration(entry, bool(refs) or resolution in ("1080p", "4k"))
    rate = MODELS.get(model, {}).get(resolution)
    if rate is None and model in MODELS:
        reasons.append(f"{model} has no {resolution} output")
    est = round((rate or 0) * duration, 2)
    spent = day_spend(root)
    needs_confirm = []
    if est > cfg["max_call_usd"]:
        needs_confirm.append(f"${est:.2f} is over the per-call cap "
                             f"${cfg['max_call_usd']:.2f}")
    if spent + est > cfg["max_day_usd"]:
        needs_confirm.append(f"today's ${spent:.2f} + ${est:.2f} is over the "
                             f"per-day cap ${cfg['max_day_usd']:.2f}")
    desc = desc_hash(spec, fields)
    lawh = _hash(law, 4)
    fresh = [t for t in takes(root, key, desc, lawh) if not t["stale"]]
    return {"key": key, "file": entry["file"], "model": model,
            "resolution": resolution, "aspect_ratio": cfg["aspect_ratio"],
            "duration": duration, "est_usd": est, "day_spent_usd": spent,
            "prompt": compose_prompt(spec, law, fields),
            "first_frame": str(first) if first else None,
            "last_frame": str(last) if last else None,
            "references": [str(p) for p in refs],
            "desc": desc, "law": lawh, "reasons": reasons,
            "needs_confirm": needs_confirm, "fresh_takes": fresh}


# ------------------------------------------------------------------ the API

def _inline(path: str) -> dict:
    data = Path(path).read_bytes()
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return {"inlineData": {"mimeType": mime,
                           "data": base64.b64encode(data).decode("ascii")}}


def request_body(p: dict) -> dict:
    instance: dict = {"prompt": p["prompt"]}
    if p["first_frame"]:
        instance["image"] = _inline(p["first_frame"])
    if p["last_frame"]:
        instance["lastFrame"] = _inline(p["last_frame"])
    if p["references"]:
        instance["referenceImages"] = [{"image": _inline(r),
                                        "referenceType": "asset"}
                                       for r in p["references"]]
    images = bool(p["first_frame"] or p["references"])
    return {"instances": [instance],
            "parameters": {"aspectRatio": p["aspect_ratio"],
                           "resolution": p["resolution"],
                           "durationSeconds": str(p["duration"]),
                           "numberOfVideos": 1,
                           "personGeneration": ("allow_adult" if images
                                                else "allow_all")}}


def _http(method: str, url: str, key: str, body: dict | None = None,
          raw: bool = False, timeout: int = 120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"Veo {method} {url.split('?')[0]} failed: "
                           f"{err.code} {detail}") from err
    return payload if raw else json.loads(payload or b"{}")


def _key() -> str:
    import os

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("no GEMINI_API_KEY — set it in authorlm/.env "
                           "(Veo is billed through the Gemini API)")
    return key


def start(db, manuscript: dict, key: str, model: str | None = None,
          confirm: bool = False, http=None) -> dict:
    """Start a generation (paid). Declines when the gate has reasons, or
    when a cap is passed without `confirm`. Records the job and returns it."""
    p = plan(db, manuscript, key, model)
    if p["reasons"]:
        raise ValueError(f"{key} is not ready to generate: "
                         + "; ".join(p["reasons"]))
    if p["needs_confirm"] and not confirm:
        raise ValueError("over a spend cap — " + "; ".join(p["needs_confirm"])
                         + " — rerun with --confirm")
    http = http or _http
    op = http("POST", f"{BASE_URL}/models/{p['model']}:predictLongRunning",
              _key(), request_body(p))
    root = Path(manuscript["path"])
    jobs = root / media.CLIPS_DIR / JOBS_SUBDIR
    jobs.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    job_id = f"{key}-{now.strftime('%Y%m%d%H%M%S')}"
    job = {"id": job_id, "key": key, "operation": op["name"], "state": "running",
           "model": p["model"], "duration": p["duration"],
           "resolution": p["resolution"], "est_usd": p["est_usd"],
           "desc": p["desc"], "law": p["law"],
           "started_local": now.strftime("%Y-%m-%d %H:%M:%S"),
           "started_utc": datetime.now(timezone.utc).isoformat(),
           "prompt": p["prompt"], "take": None, "error": None}
    (jobs / f"{job_id}.json").write_text(json.dumps(job, indent=1),
                                         encoding="utf-8")
    # The request as sent, with image paths in place of their bytes, so a
    # caller (podlm) can see what shaped a take.
    sent = request_body(p)
    sent["instances"][0] = {"prompt": p["prompt"],
                            "first_frame": p["first_frame"],
                            "last_frame": p["last_frame"],
                            "references": p["references"]}
    (jobs / f"{job_id}.request.json").write_text(json.dumps(sent, indent=1),
                                                 encoding="utf-8")
    return job


def job_path(root: Path, job_id: str) -> Path:
    return Path(root) / media.CLIPS_DIR / JOBS_SUBDIR / f"{job_id}.json"


def poll(manuscript: dict, job_id: str, http=None) -> dict:
    """Check a job; when Veo is done, download the take, choose it if the
    clip has no chosen take yet, and record its path. Paths only."""
    root = Path(manuscript["path"])
    path = job_path(root, job_id)
    if not path.exists():
        raise LookupError(f"no job {job_id}")
    job = json.loads(path.read_text(encoding="utf-8"))
    if job["state"] in ("done", "failed"):
        return job
    http = http or _http
    op = http("GET", f"{BASE_URL}/{job['operation']}", _key())
    (path.parent / f"{job_id}.response.json").write_text(
        json.dumps(op, indent=1), encoding="utf-8")
    if not op.get("done"):
        return job
    if op.get("error"):
        job.update(state="failed", error=json.dumps(op["error"])[:500])
    else:
        samples = (op.get("response", {}).get("generateVideoResponse", {})
                   .get("generatedSamples") or [])
        if not samples:
            job.update(state="failed", error="Veo returned no video (it may "
                       "have been blocked by a safety filter; blocked videos "
                       "are not charged)")
        else:
            blob = http("GET", samples[0]["video"]["uri"], _key(), raw=True)
            name = (f"{job['key']}-{job['desc']}-{job['law']}-"
                    f"{_next_n(root, job['key'])}.mp4")
            (root / media.CLIPS_DIR / name).write_bytes(blob)
            if not chosen(root, job["key"]):
                _set_chosen(root, job["key"], name)
            job.update(state="done", take=f"{media.CLIPS_DIR}/{name}")
    path.write_text(json.dumps(job, indent=1), encoding="utf-8")
    return job
