"""Generated clips: plan, start, poll, take (docs/video-clips-design.md
§8b, §8c, §8f, §8j; slice 3).

A generated clip ([Clip] with a generation spec, no `youtube:`/`use:`) is
made by Veo or Gemini Omni through the Gemini API, over plain REST as
`llm._gemini_image` is. Veo is a `predictLongRunning` operation; Omni is a
background interaction (`POST /interactions`, `background: true`), polled
until its status leaves `in_progress`, its video inline in `steps[]`. The road is the design's:

- `plan` composes everything a generation would send and what it costs,
  and runs the gate. It spends nothing: it IS the dry run.
- `start` sends the request and records a job in `_clips/jobs/<id>.json`;
  generation takes seconds to minutes, so it never waits.
- `poll` checks a job and, once the model is done, downloads the take to
  `_clips/<key>-<deschash8>-<lawhash4>-NN.mp4`. The first take of a clip
  is chosen automatically (recorded in `_clips/chosen.json`, the store of
  record) so a caller always gets a path; `pick` moves the choice,
  `attach` brings in an outside file as `iNN`.
- `edit_plan`/`edit_start` revise an Omni take by instruction: a new
  interaction continuing the take's (`previous_interaction_id`), landing
  as take `eNN`. It carries its parent's hashes — it is that spec's
  picture, revised — and its job records the parent and the instruction.

The deschash fingerprints the spec and the fields that shape the picture;
the lawhash the effective `clip` law. A take whose hashes differ from the
clip's current ones is stale (`takes`). Nothing regenerates by itself.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
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
    # 5,792 video tokens per 720p second at $17.50/1M. Omni's 1080p and 4k
    # are upscales Google does not price, so they are not offered.
    "gemini-omni-1.1-flash": {"720p": 0.10},
}
# Omni's token rates, USD per token, for the cost a finished job reports.
OMNI_RATES = {"input": 1.50e-6, "text": 9.00e-6, "video": 17.50e-6}
OMNI_TOKENS_PER_SECOND = 5792   # 720p video, as output and as input
DEFAULTS = {"model": "veo-3.1-fast-generate-preview", "resolution": "720p",
            "aspect_ratio": "16:9", "max_call_usd": 2.0, "max_day_usd": 10.0}
DURATIONS = (4, 6, 8)
OMNI_SECONDS = (3, 10)   # Omni takes any whole length in this range


def is_omni(model: str) -> bool:
    return model.startswith("gemini-omni")
TAKE = re.compile(r"^(?P<key>.+)-(?P<desc>[0-9a-f]{8})-(?P<law>[0-9a-f]{4})-"
                  r"(?P<n>[ie]?\d{2})\.(?P<ext>mp4|mov|m4v|webm)$")


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
    return sorted(out, key=lambda t: (t["n"].lstrip("ie").zfill(3), t["n"]))


def _next_n(root: Path, key: str, prefix: str = "") -> str:
    """The next take number; prefix `i` for imported, `e` for edited.

    Take names carry exactly two digits (`TAKE`); past 99 the next name
    would be invisible to `takes` and every later save would overwrite the
    same paid bytes. Refuse rather than destroy."""
    numbers = [int(t["n"].lstrip("ie")) for t in takes(root, key)]
    n = (max(numbers) + 1) if numbers else 1
    if n > 99:
        raise RuntimeError(
            f"{key} already has 99 takes — pick or remove one before "
            "generating or editing another")
    return f"{prefix}{n:02d}"


def _write_new_job(jobs: Path, key: str, job: dict, request: dict) -> str:
    """Create `_clips/jobs/<id>.json` under an unused id and write it.

    Ids are `{key}-{YYYYMMDDHHMMSS}` with a numeric suffix on collision.
    A second-granularity stamp alone lets two starts (or a start and an
    edit) in the same wall-clock second overwrite the first job file and
    orphan a billed Gemini interaction."""
    jobs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    for n in range(1000):
        job_id = f"{key}-{stamp}" if n == 0 else f"{key}-{stamp}-{n}"
        path = jobs / f"{job_id}.json"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        job["id"] = job_id
        try:
            os.write(fd, json.dumps(job, indent=1).encode("utf-8"))
        finally:
            os.close(fd)
        (jobs / f"{job_id}.request.json").write_text(
            json.dumps(request, indent=1), encoding="utf-8")
        return job_id
    raise RuntimeError(f"could not allocate a job id for {key}")


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
            f"{_next_n(root, clip['ref'][:-3], "i")}.{ext}")
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


def _duration(entry: dict, forced_eight: bool, omni: bool = False) -> int:
    if omni:
        # Omni has no duration field; the prompt's timecode sets the length
        # (a "[0-4s]" prompt gave exactly 4 s, live 2026-10-22).
        want = entry.get("estimated_seconds") or 8
        return max(OMNI_SECONDS[0], min(OMNI_SECONDS[1], int(-(-want // 1))))
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


def omni_prompt(prompt: str, duration: int, first: bool, last: bool,
                refs: int) -> str:
    """Omni's additions to the composed prompt: the timecode that sets the
    length, which input image is which, and the reference tags."""
    lines = [f"[0-{duration}s]"]
    n = 0
    if first:
        lines.append(f"Begin on <IMAGE_REF_{n}> as the first frame.")
        n += 1
    if last:
        lines.append(f"End on <IMAGE_REF_{n}> as the last frame.")
        n += 1
    if refs:
        tags = ", ".join(f"<IMAGE_REF_{n + i}>" for i in range(refs))
        lines.append(f"Keep the people and objects in {tags} as shown.")
    lines.append("No dialogue.")
    return "\n".join(lines) + "\n\n" + prompt


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
    omni = is_omni(model)
    duration = _duration(entry, bool(refs) or resolution in ("1080p", "4k"),
                         omni)
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
    prompt = compose_prompt(spec, law, fields)
    if omni:
        prompt = omni_prompt(prompt, duration, bool(first), bool(last),
                             len(refs))
    return {"key": key, "file": entry["file"], "model": model,
            "resolution": resolution, "aspect_ratio": cfg["aspect_ratio"],
            "duration": duration, "est_usd": est, "day_spent_usd": spent,
            "prompt": prompt,
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
                           "durationSeconds": int(p["duration"]),
                           "personGeneration": ("allow_adult" if images
                                                else "allow_all")}}


def omni_body(p: dict) -> dict:
    """The Interactions API request: text, then the first frame, last frame
    and references in the order omni_prompt's tags count them."""
    images = [x for x in (p["first_frame"], p["last_frame"]) if x]
    images += p["references"]
    inputs: list = [{"type": "text", "text": p["prompt"]}]
    for path in images:
        inline = _inline(path)["inlineData"]
        inputs.append({"type": "image", "data": inline["data"],
                       "mime_type": inline["mimeType"]})
    return {"model": p["model"], "background": True,
            "input": inputs if images else p["prompt"],
            "response_format": {"type": "video",
                                "aspect_ratio": p["aspect_ratio"],
                                "resolution": p["resolution"]}}


def _scrub(value):
    """A response with its base64 blobs replaced by their length."""
    if isinstance(value, dict):
        return {k: (f"<{len(v)} base64 chars>"
                    if k == "data" and isinstance(v, str) else _scrub(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def omni_usd(usage: dict) -> float:
    """What a finished Omni interaction cost, from its token counts."""
    out = {m["modality"]: m["tokens"]
           for m in usage.get("output_tokens_by_modality", [])}
    video = out.get("video", 0)
    text = usage.get("total_output_tokens", 0) - video \
        + usage.get("total_thought_tokens", 0)
    return round(usage.get("total_input_tokens", 0) * OMNI_RATES["input"]
                 + text * OMNI_RATES["text"]
                 + video * OMNI_RATES["video"], 2)


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
        raise RuntimeError(f"Gemini API {method} {url.split('?')[0]} failed: "
                           f"{err.code} {detail}") from err
    return payload if raw else json.loads(payload or b"{}")


def _key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("no GEMINI_API_KEY — set it in authorlm/.env "
                           "(Veo and Omni are billed through the Gemini API)")
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
    omni = is_omni(p["model"])
    if omni:
        op = http("POST", f"{BASE_URL}/interactions", _key(), omni_body(p))
        op = {"name": f"interactions/{op['id']}"}
    else:
        op = http("POST", f"{BASE_URL}/models/{p['model']}:predictLongRunning",
                  _key(), request_body(p))
    root = Path(manuscript["path"])
    jobs = root / media.CLIPS_DIR / JOBS_SUBDIR
    now = datetime.now()
    job = {"id": "", "key": key, "operation": op["name"], "state": "running",
           "model": p["model"], "duration": p["duration"],
           "resolution": p["resolution"], "est_usd": p["est_usd"],
           "desc": p["desc"], "law": p["law"],
           "started_local": now.strftime("%Y-%m-%d %H:%M:%S"),
           "started_utc": datetime.now(timezone.utc).isoformat(),
           "prompt": p["prompt"], "take": None, "error": None}
    # The request as sent, with image paths in place of their bytes, so a
    # caller (podlm) can see what shaped a take.
    images = {"prompt": p["prompt"], "first_frame": p["first_frame"],
              "last_frame": p["last_frame"], "references": p["references"]}
    if omni:
        sent = omni_body(p)
        sent["input"] = images
    else:
        sent = request_body(p)
        sent["instances"][0] = images
    _write_new_job(jobs, key, job, sent)
    return job


def job_path(root: Path, job_id: str) -> Path:
    return Path(root) / media.CLIPS_DIR / JOBS_SUBDIR / f"{job_id}.json"


def poll(manuscript: dict, job_id: str, http=None) -> dict:
    """Check a job; when the model is done, download the take, choose it if the
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
        json.dumps(_scrub(op), indent=1), encoding="utf-8")
    if job["operation"].startswith("interactions/"):
        return _poll_omni(root, path, job, op)
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
            _save_take(root, job, blob)
    path.write_text(json.dumps(job, indent=1), encoding="utf-8")
    return job


def _save_take(root: Path, job: dict, blob: bytes) -> None:
    """Land the paid bytes under a new take name.

    Exclusive create so two overlapping polls for the same clip cannot
    both compute the same next N and destroy each other's take."""
    prefix = "e" if job.get("edit_of") else ""
    clips = root / media.CLIPS_DIR
    clips.mkdir(parents=True, exist_ok=True)
    while True:
        name = (f"{job['key']}-{job['desc']}-{job['law']}-"
                f"{_next_n(root, job['key'], prefix)}.mp4")
        path = clips / name
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        try:
            os.write(fd, blob)
        finally:
            os.close(fd)
        break
    if not chosen(root, job["key"]):
        _set_chosen(root, job["key"], name)
    job.update(state="done", take=f"{media.CLIPS_DIR}/{name}")


def _poll_omni(root: Path, path: Path, job: dict, op: dict) -> dict:
    status = op.get("status")
    if status == "in_progress":
        return job
    if status == "completed":
        videos = [c for s in op.get("steps", [])
                  if s.get("type") == "model_output"
                  for c in s.get("content", []) if c.get("type") == "video"]
        if videos and videos[0].get("data"):
            _save_take(root, job, base64.b64decode(videos[0]["data"]))
            job["usd"] = omni_usd(op.get("usage", {}))
        else:
            job.update(state="failed", error="Omni returned no video (it "
                       "may have been blocked by a safety filter)")
    else:
        job.update(state="failed", error=f"Omni interaction {status}: "
                   + json.dumps(_scrub(op.get("error") or {}))[:400])
    path.write_text(json.dumps(job, indent=1), encoding="utf-8")
    return job


# ------------------------------------------------------------------ edits

EDIT_TAIL = "Keep everything else the same."


def _jobs(root: Path) -> list[dict]:
    directory = Path(root) / media.CLIPS_DIR / JOBS_SUBDIR
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(directory.glob("*.json"))
            if not p.name.endswith((".request.json", ".response.json"))]


def edit_plan(manuscript: dict, key: str, take: str, instruction: str) -> dict:
    """What revising a take would send and cost, and the gate. Spends
    nothing. Only a take Omni made can be revised: its interaction is the
    state Omni edits from (Google keeps stored interactions for a limited
    time, so an old take's edit can fail at start)."""
    root = Path(manuscript["path"])
    clip = media.find_clip(root, key)
    ckey = clip["ref"][:-3]
    match = [t for t in takes(root, ckey)
             if t["n"] == str(take) or t["n"] == str(take).zfill(2)]
    if not match:
        raise LookupError(f"{key} has no take {take} — 'media list --key "
                          f"{key}' shows its takes")
    parent = match[0]
    source = next((j for j in _jobs(root) if j.get("take") == parent["path"]),
                  None)
    reasons = []
    if not instruction.strip():
        reasons.append("an edit needs an instruction")
    if not source or not source["operation"].startswith("interactions/"):
        reasons.append(f"take {parent['n']} was not made by Omni — only an "
                       "Omni take can be revised")
    cfg = settings(root)
    duration = int((source or {}).get("duration") or OMNI_SECONDS[1])
    model = (source or {}).get("model") or "gemini-omni-1.1-flash"
    # The new video, plus the parent re-read as input video tokens (live
    # 2026-10-22: a 4 s edit took 23,405 input video tokens).
    est = round(MODELS.get(model, {}).get("720p", 0.10) * duration
                + duration * OMNI_TOKENS_PER_SECOND * OMNI_RATES["input"], 2)
    spent = day_spend(root)
    needs_confirm = []
    if est > cfg["max_call_usd"]:
        needs_confirm.append(f"${est:.2f} is over the per-call cap "
                             f"${cfg['max_call_usd']:.2f}")
    if spent + est > cfg["max_day_usd"]:
        needs_confirm.append(f"today's ${spent:.2f} + ${est:.2f} is over the "
                             f"per-day cap ${cfg['max_day_usd']:.2f}")
    m = TAKE.match(parent["name"])
    return {"key": ckey, "take": parent["path"], "model": model,
            "previous_interaction_id": (source or {}).get(
                "operation", "").removeprefix("interactions/"),
            "prompt": f"{instruction.strip()} {EDIT_TAIL}",
            "instruction": instruction.strip(), "duration": duration,
            "resolution": cfg["resolution"],
            "aspect_ratio": cfg["aspect_ratio"], "est_usd": est,
            "day_spent_usd": spent, "desc": m.group("desc"),
            "law": m.group("law"), "reasons": reasons,
            "needs_confirm": needs_confirm}


def edit_body(p: dict) -> dict:
    return {"model": p["model"], "background": True,
            "previous_interaction_id": p["previous_interaction_id"],
            "input": p["prompt"],
            "response_format": {"type": "video",
                                "aspect_ratio": p["aspect_ratio"],
                                "resolution": "720p"}}


def edit_start(manuscript: dict, key: str, take: str, instruction: str,
               confirm: bool = False, http=None) -> dict:
    """Start revising a take (paid). Same gate and caps as `start`; poll
    the returned job with `poll`, and the revision lands as take eNN."""
    p = edit_plan(manuscript, key, take, instruction)
    if p["reasons"]:
        raise ValueError(f"take {take} of {key} cannot be revised: "
                         + "; ".join(p["reasons"]))
    if p["needs_confirm"] and not confirm:
        raise ValueError("over a spend cap — " + "; ".join(p["needs_confirm"])
                         + " — rerun with --confirm")
    http = http or _http
    body = edit_body(p)
    op = http("POST", f"{BASE_URL}/interactions", _key(), body)
    root = Path(manuscript["path"])
    jobs = root / media.CLIPS_DIR / JOBS_SUBDIR
    now = datetime.now()
    job = {"id": "", "key": p["key"],
           "operation": f"interactions/{op['id']}",
           "state": "running", "model": p["model"], "duration": p["duration"],
           "resolution": "720p", "est_usd": p["est_usd"], "desc": p["desc"],
           "law": p["law"], "started_local": now.strftime("%Y-%m-%d %H:%M:%S"),
           "started_utc": datetime.now(timezone.utc).isoformat(),
           "prompt": p["prompt"], "edit_of": p["take"],
           "instruction": p["instruction"], "take": None, "error": None}
    _write_new_job(jobs, p["key"], job, body)
    return job
