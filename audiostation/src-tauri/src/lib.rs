mod book;
mod commands;
mod stitch;
mod types;
mod watcher;

use std::sync::{Arc, Mutex};

pub fn run() {
    let shared_master: commands::SharedMaster = Arc::new(Mutex::new(None));
    let shared_watcher: watcher::SharedWatcher = Arc::new(Mutex::new(None));

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
        .manage(shared_master)
        .manage(shared_watcher)
        .setup(|app| {
            // `authorlm audiostation` (and `open -a audiostation --args DIR`)
            // hand the audiobook folder on the command line; open it before
            // the window asks for the last folder, so the frontend's restore
            // finds it in the store.
            if let Some(dir) = std::env::args().nth(1).filter(|a| !a.starts_with('-')) {
                match commands::open_from_argv(app.handle(), &dir) {
                    Ok(()) => log::info!("[argv] opened {dir}"),
                    Err(e) => log::warn!("[argv] could not open {dir}: {e}"),
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::open_book,
            commands::reload_book,
            commands::close_book,
            commands::get_master,
            commands::get_last_book,
            commands::get_settings,
            commands::save_settings,
            commands::generate_section,
            commands::generate_all_remaining,
            commands::stitch_audio,
            commands::clear_lower_quality,
            commands::run_acx_audit,
            commands::generate_acx_package,
            commands::get_subscription,
            commands::read_audio_base64,
            commands::reveal_in_finder,
            commands::get_log_path,
            commands::log_frontend_error,
            commands::log_frontend_info,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
