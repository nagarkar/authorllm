/// Axum HTTP server listening on 127.0.0.1:3847.
///
/// Endpoints
/// ---------
/// POST /partial-manifest  — receive a PartialManifest from the GAS add-on
/// GET  /status            — liveness probe so GAS can check the app is running
use axum::{
    extract::State,
    http::StatusCode,
    response::Json,
    routing::{get, post},
    Router,
};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, Manager};
use tower_http::cors::{Any, CorsLayer};

use crate::types::{MasterManifest, PartialManifest, PartialManifestInfo, StatusResponse};

pub type SharedManifest = Arc<Mutex<Option<MasterManifest>>>;

#[derive(Clone)]
pub struct AppState {
    pub manifest: SharedManifest,
    pub app_handle: AppHandle,
}

/// Start the HTTP server in a background tokio task.
/// This function returns immediately; the server runs until the process exits.
pub fn start(state: AppState) {
    tauri::async_runtime::spawn(async move {
        let cors = CorsLayer::new()
            .allow_origin(Any)
            .allow_methods(Any)
            .allow_headers(Any);

        let router = Router::new()
            .route("/partial-manifest", post(handle_partial_manifest))
            .route("/manifest", post(handle_partial_manifest))  // legacy compat
            .route("/status", get(handle_status))
            .layer(cors)
            .with_state(state);

        let listener = tokio::net::TcpListener::bind("127.0.0.1:3847")
            .await
            .expect("Failed to bind to 127.0.0.1:3847 — is another instance already running?");

        log::info!("[http_server] Listening on http://127.0.0.1:3847");
        axum::serve(listener, router)
            .await
            .expect("HTTP server error");
    });
}

/// POST /partial-manifest
///
/// Does NOT store in shared state directly — instead emits `partial-manifest-received`
/// to the Tauri frontend which handles the confirm/merge flow.
async fn handle_partial_manifest(
    State(state): State<AppState>,
    Json(partial): Json<PartialManifest>,
) -> (StatusCode, Json<serde_json::Value>) {
    let document_title = partial.inner()
        .map(|m| m.document_title.clone())
        .unwrap_or_else(|| "Unknown".to_string());

    let info = PartialManifestInfo {
        kind: partial.kind().to_string(),
        display_name: partial.display_name(),
        document_title: document_title.clone(),
    };

    log::info!(
        "[http_server] Partial manifest received: {} ({})",
        info.display_name,
        info.kind
    );

    let payload = serde_json::json!({ "info": info, "partial": partial });

    if let Err(e) = state.app_handle.emit("partial-manifest-received", &payload) {
        log::warn!("[http_server] Failed to emit partial-manifest-received: {e}");
    }

    // Bring the app window to the front so the user notices the new manifest.
    if let Some(window) = state.app_handle.get_webview_window("main") {
        let _ = window.set_focus();
    }

    (
        StatusCode::OK,
        Json(serde_json::json!({ "ok": true })),
    )
}

/// GET /status
///
/// Returns a simple JSON body that GAS polls before attempting to POST a manifest.
async fn handle_status() -> Json<StatusResponse> {
    Json(StatusResponse {
        running: true,
        version: env!("CARGO_PKG_VERSION"),
    })
}
