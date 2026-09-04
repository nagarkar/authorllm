mod commands;
mod http_server;
mod stitch;
mod types;

use std::sync::{Arc, Mutex};

pub fn run() {
    let shared_manifest:  commands::SharedManifest  = Arc::new(Mutex::new(None));
    let shared_file_path: commands::SharedFilePath  = Arc::new(Mutex::new(None));

    let log_level = if cfg!(debug_assertions) {
        log::LevelFilter::Debug
    } else {
        log::LevelFilter::Info
    };

    tauri::Builder::default()
        .plugin(
            tauri_plugin_log::Builder::new()
                .targets([
                    tauri_plugin_log::Target::new(
                        tauri_plugin_log::TargetKind::LogDir { file_name: Some("app".into()) }
                    ),
                    tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::Stdout),
                ])
                .level(log_level)
                .build(),
        )
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(shared_manifest.clone())
        .manage(shared_file_path.clone())
        .setup(move |app| {
            let http_state = http_server::AppState {
                manifest: shared_manifest.clone(),
                app_handle: app.handle().clone(),
            };
            http_server::start(http_state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // Manifest commands (new names)
            commands::get_master_manifest,
            commands::set_master_manifest,
            // Legacy aliases for backward compat
            commands::get_manifest,
            commands::set_manifest,
            commands::clear_manifest,
            commands::set_manifest_file_path,
            commands::update_section,
            commands::update_silence_section,
            commands::save_manifest_to_file,
            commands::generate_section,
            commands::generate_all_remaining,
            commands::stitch_audio,
            commands::get_settings,
            commands::save_settings,
            commands::get_blocked_sections,
            commands::reveal_in_finder,
            commands::get_log_path,
            commands::log_frontend_error,
            commands::log_frontend_info,
            commands::get_dictionary_info,
            commands::resolve_dictionary_to_latest,
            commands::list_voices,
            commands::read_audio_base64,
            commands::load_manifest_from_file,
            commands::get_last_file,
            commands::save_last_file,
            commands::clear_lower_quality,
            commands::insert_silence,
            commands::insert_speech,
            commands::add_silences_between_speech,
            commands::delete_section,
            commands::get_subscription,
            commands::list_dictionaries,
            commands::set_dictionary,
            // Raw Edits panel
            commands::check_file_paths,
            // New ACX commands
            commands::merge_partial_into_master,
            commands::set_cover_image,
            commands::add_retail_sample_ref,
            commands::remove_retail_sample_ref,
            commands::run_acx_audit,
            commands::generate_acx_package,
            commands::set_book_metadata,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
