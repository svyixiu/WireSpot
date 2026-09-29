//! WireSpot desktop app: the window, the tray and the startup splash. All the
//! networking happens in the WireSpot engine (Python), see engine.rs.

use std::env;
use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;
use tauri::{AppHandle, Emitter, Manager};

mod brand;
mod engine;
mod notify;
mod payload;
mod tray;

/// The folder WireSpot.exe runs from.
fn exe_dir() -> PathBuf {
    env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_default()
}

/// WireSpot.exe started from anywhere but its install folder shows its
/// installer first (like Questly). A portable folder (settings.json or vpn\
/// next to the exe) and development builds run as they are.
fn starts_in_setup() -> bool {
    let args: Vec<String> = env::args().skip(1).collect();
    let has = |flag: &str| args.iter().any(|a| a == flag);
    if has("--setup") {
        return true;
    }
    if cfg!(debug_assertions) || has("--portable") || has("--uninstall") || has("--autostart") || has("--background") {
        return false;
    }
    let dir = exe_dir();
    !(dir.join("install.json").is_file() || dir.join("settings.json").is_file() || dir.join("vpn").is_dir())
}

/// Resolves a path the way the process that sent it saw it.
fn resolve(path: &str, cwd: &str) -> PathBuf {
    let p = PathBuf::from(path);
    if p.is_absolute() {
        p
    } else {
        Path::new(cwd).join(p)
    }
}

fn same_file(a: &Path, b: &Path) -> bool {
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        _ => false,
    }
}

/// Quits, then starts `exe` once this process is gone (it can't start while
/// this one still holds the single-instance lock).
fn quit_and_start(app: &AppHandle, exe: &Path, args: &[String]) -> Result<(), String> {
    let quoted_args = args.iter().map(|a| ps_quote(a)).collect::<Vec<_>>().join(",");
    let script = format!(
        "Wait-Process -Id {pid} -ErrorAction SilentlyContinue; Start-Process -FilePath {exe}{args}",
        pid = std::process::id(),
        exe = ps_quote(&exe.to_string_lossy()),
        args = if args.is_empty() { String::new() } else { format!(" -ArgumentList {}", quoted_args) },
    );
    let mut cmd = std::process::Command::new("powershell");
    cmd.args(["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", &script]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000 | 0x0000_0008); // CREATE_NO_WINDOW | DETACHED_PROCESS
    }
    cmd.spawn().map_err(|e| format!("Couldn't start {}: {}", exe.display(), e))?;
    quit(app);
    Ok(())
}

pub(crate) fn main_window_visible(app: &AppHandle) -> bool {
    app.get_webview_window("main")
        .and_then(|w| w.is_visible().ok())
        .unwrap_or(false)
}

pub(crate) fn show_main_window(app: &AppHandle) {
    // the window shows waiting devices and messages itself
    notify::hide(app);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
    // the engine polls faster and asks its questions in the window while it's open
    app.state::<engine::Engine>().notify("visible", serde_json::json!({ "visible": true }));
}

pub(crate) fn hide_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
    app.state::<engine::Engine>().notify("visible", serde_json::json!({ "visible": false }));
}

pub(crate) fn toggle_main_window(app: &AppHandle) {
    if main_window_visible(app) {
        hide_main_window(app);
    } else {
        show_main_window(app);
    }
}

/// Quit WireSpot: the engine stops, but the VPN and hotspot keep running.
pub(crate) fn quit(app: &AppHandle) {
    // take the tray icon down first: it goes away at once, and can't be left
    // behind in the notification area if the exit doesn't finish
    if let Some(tray) = app.tray_by_id("main") {
        let _ = tray.set_visible(false);
    }
    let app = app.clone();
    std::thread::spawn(move || {
        engine::shutdown(&app);
        app.exit(0);
    });
}

fn env_dir(var: &str) -> Option<PathBuf> {
    env::var_os(var).map(PathBuf::from)
}

/// %LOCALAPPDATA%\WireSpot: machine-local files of the app (icons).
pub(crate) fn app_local_dir() -> PathBuf {
    env_dir("LOCALAPPDATA").unwrap_or_else(env::temp_dir).join("WireSpot")
}

/// Where the installer puts WireSpot.exe.
pub(crate) fn installed_exe() -> PathBuf {
    env_dir("LOCALAPPDATA")
        .unwrap_or_else(env::temp_dir)
        .join("Programs")
        .join("WireSpot")
        .join("WireSpot.exe")
}

/// Runs a program without flashing a console window.
pub(crate) fn run_hidden(program: &str, args: &[&str]) -> Result<(), String> {
    let mut cmd = std::process::Command::new(program);
    cmd.args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let output = cmd.output().map_err(|e| format!("Failed to run {}: {}", program, e))?;
    if output.status.success() {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
    }
}

/// Single-quoted PowerShell string literal.
pub(crate) fn ps_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "''"))
}

#[tauri::command]
fn hide_window(app: AppHandle) {
    hide_main_window(&app);
}

#[tauri::command]
fn show_window(app: AppHandle) {
    show_main_window(&app);
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    quit(&app);
}

#[tauri::command]
fn launch_args() -> Vec<String> {
    env::args().skip(1).collect()
}

/// Which screen to show, and the facts the installer needs.
#[tauri::command]
fn shell_info(app: AppHandle) -> serde_json::Value {
    serde_json::json!({
        "setup": app.state::<engine::Engine>().setup.load(Ordering::Relaxed),
        "version": app.package_info().version.to_string(),
        "exe": env::current_exe().map(|p| p.to_string_lossy().to_string()).unwrap_or_default(),
        "installed_exe": installed_exe().to_string_lossy(),
        "installed_exists": installed_exe().is_file(),
        "dev": cfg!(debug_assertions),
    })
}

/// "Run without installing": leave the installer and start WireSpot as it is.
#[tauri::command]
fn run_portable(app: AppHandle) -> Result<(), String> {
    let engine = app.state::<engine::Engine>();
    if engine.setup.swap(false, Ordering::Relaxed) {
        engine::restart(&app);
    }
    if app.tray_by_id("main").is_none() {
        tray::create(&app).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Opens the installed copy (after installing it) and closes this one.
#[tauri::command]
fn launch_installed(app: AppHandle) -> Result<(), String> {
    let target = installed_exe();
    if !target.is_file() {
        return Err("WireSpot isn't installed yet.".into());
    }
    quit_and_start(&app, &target, &[])
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // A second launch: `--quit` closes this one; another copy of WireSpot.exe
        // (usually a newer download) takes over, so its installer can update this
        // one; the same copy just brings this one forward (passing --uninstall along).
        // Only processes at least as trusted as this one can send these (Windows
        // blocks messages to an administrator window from normal programs).
        .plugin(tauri_plugin_single_instance::init(|app, argv, cwd| {
            if argv.iter().any(|a| a == "--quit") {
                quit(app);
                return;
            }
            if let (Some(first), Ok(this)) = (argv.first(), env::current_exe()) {
                let other = resolve(first, &cwd);
                let is_exe = other.extension().is_some_and(|e| e.eq_ignore_ascii_case("exe"));
                if is_exe && other.is_file() && !same_file(&other, &this) {
                    let _ = quit_and_start(app, &other, &argv[1..]);
                    return;
                }
            }
            show_main_window(app);
            if argv.iter().any(|a| a == "--uninstall") {
                let _ = app.emit("engine", serde_json::json!({ "event": "uninstall_prompt", "data": null }));
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .manage(engine::Engine::default())
        .setup(|app| {
            let setup = starts_in_setup();
            app.state::<engine::Engine>().setup.store(setup, Ordering::Relaxed);
            // the installer has no tray icon; the app gets it (also after "Run without installing")
            if !setup {
                tray::create(app.handle())?;
            }
            engine::start(app.handle().clone());
            // Start with Windows opens WireSpot in the tray only: no splash, no window
            let background = env::args().any(|a| a == "--background" || a == "--autostart");
            if !background {
                brand::open_splash(app);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // closing the window keeps WireSpot running in the tray, guarding the hotspot
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let app = window.app_handle();
                    // the installer has no tray to wait in: closing it quits
                    if app.state::<engine::Engine>().setup.load(Ordering::Relaxed) {
                        quit(app);
                    } else {
                        hide_main_window(app);
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            engine::engine,
            engine::engine_state,
            hide_window,
            show_window,
            quit_app,
            launch_args,
            shell_info,
            run_portable,
            launch_installed,
            notify::notify_state,
            notify::notify_resize,
            brand::splash_stage,
            brand::get_splash_stage,
            brand::finish_splash,
            brand::set_brand_icon,
            brand::set_shortcut_icon,
        ])
        .run(tauri::generate_context!())
        .expect("error while running WireSpot");
}
