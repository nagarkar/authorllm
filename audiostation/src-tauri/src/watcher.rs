//! Watch the audiobook folder the way AuthorLM's `collect` watches the
//! manuscript: when `audiobook.json` or anything under `chapters/`
//! changes, reload, diff, and tell the window (design §10.3).
//!
//! `state/` is watched too since 2026-09-25: AuthorLM's `audio generate`
//! is a second writer of the same state (docs/audiobook-review-design.md
//! §5, guard 1), and a reload is how this app learns of a take it did not
//! make. A reload repaints and never generates. Our own writes come back
//! through the watcher as well; they reload to a state equal to the one
//! in memory, so the burst is dropped without an event. `audio/` is not
//! watched: a take is only real once its state entry says so.

use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use notify::{RecommendedWatcher, RecursiveMode, Watcher};
use tauri::{AppHandle, Emitter, Manager};

use crate::commands::{reload_internal, SharedMaster};
use crate::types::{BOOK_FILE, CHAPTERS_DIR, STATE_DIR};

pub type SharedWatcher = Arc<Mutex<Option<RecommendedWatcher>>>;

const DEBOUNCE: Duration = Duration::from_millis(400);

fn interesting(path: &Path, dir: &Path) -> bool {
    if path == dir.join(BOOK_FILE) {
        return true;
    }
    (path.starts_with(dir.join(CHAPTERS_DIR)) || path.starts_with(dir.join(STATE_DIR)))
        && path.extension().map_or(false, |e| e == "json")
}

fn is_state(path: &Path, dir: &Path) -> bool {
    path.starts_with(dir.join(STATE_DIR))
}

/// Start (or restart) the watcher on `dir`. Events are debounced on a
/// background thread; each burst becomes one reload and one
/// `book-changed` event carrying the reload's summary.
pub fn start(app: &AppHandle, dir: PathBuf) -> Result<(), String> {
    let (tx, rx) = mpsc::channel::<PathBuf>();
    let watch_dir = dir.clone();
    let mut watcher = notify::recommended_watcher(move |res: notify::Result<notify::Event>| {
        if let Ok(event) = res {
            for path in event.paths {
                if interesting(&path, &watch_dir) {
                    let _ = tx.send(path);
                }
            }
        }
    }).map_err(|e| format!("cannot create watcher: {e}"))?;
    watcher.watch(&dir, RecursiveMode::Recursive)
        .map_err(|e| format!("cannot watch {}: {e}", dir.display()))?;

    let slot = app.state::<SharedWatcher>();
    *slot.lock().unwrap() = Some(watcher);

    let handle = app.clone();
    let dir_for_thread = dir.clone();
    std::thread::spawn(move || {
        while let Ok(first) = rx.recv() {
            let mut paths = vec![first];
            let deadline = Instant::now() + DEBOUNCE;
            loop {
                let left = deadline.saturating_duration_since(Instant::now());
                if left.is_zero() { break; }
                match rx.recv_timeout(left) {
                    Ok(p) => paths.push(p),
                    Err(_) => break,
                }
            }
            // AuthorLM writes whole files; a half-written JSON parses as
            // an error. Wait a beat and retry once before giving up.
            let state = handle.state::<SharedMaster>();
            let mut result = reload_internal(&state);
            if result.is_err() {
                std::thread::sleep(Duration::from_millis(300));
                result = reload_internal(&state);
            }
            let state_only = paths.iter().all(|p| is_state(p, &dir_for_thread));
            match result {
                Ok(summary) => {
                    if state_only && summary.is_quiet() {
                        // Our own save, or a save that changed nothing we
                        // did not already hold: no event, no repaint.
                        continue;
                    }
                    log::info!("[watcher] reloaded after {} change(s): {}",
                               paths.len(), summary);
                    let _ = handle.emit(if state_only { "state-changed" } else { "book-changed" },
                                        &summary);
                }
                Err(e) => {
                    log::warn!("[watcher] reload failed: {e}");
                    let _ = handle.emit("book-error", &e);
                }
            }
        }
    });
    Ok(())
}

pub fn stop(app: &AppHandle) {
    let slot = app.state::<SharedWatcher>();
    *slot.lock().unwrap() = None;
}
