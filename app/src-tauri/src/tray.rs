//! The tray icon: coloured by state (live, VPN only, idle, busy, paused, needs
//! attention), a tooltip with the details, and a menu with the everyday actions.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Mutex;
use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, Wry,
};

pub struct Tray {
    status: MenuItem<Wry>,
    toggle: MenuItem<Wry>,
    pause: MenuItem<Wry>,
    icons: Mutex<HashMap<String, Image<'static>>>,
    shown: Mutex<String>,
    /// the model action the Go live / Disconnect item runs right now
    toggle_action: Mutex<String>,
}

/// Adds the tray icon (the app has one; the installer doesn't).
pub fn create(app: &AppHandle) -> tauri::Result<()> {
    let status = MenuItem::with_id(app, "status", "WireSpot · starting…", false, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "Open WireSpot", true, None::<&str>)?;
    let toggle = MenuItem::with_id(app, "toggle", "Go live", false, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause15", "Pause 15 minutes", false, None::<&str>)?;
    let copy = MenuItem::with_id(app, "copy_pw", "Copy Wi-Fi password", true, None::<&str>)?;
    let cli = MenuItem::with_id(app, "cli", "Open WireSpot CLI", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit WireSpot", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &status,
            &PredefinedMenuItem::separator(app)?,
            &open,
            &toggle,
            &pause,
            &PredefinedMenuItem::separator(app)?,
            &copy,
            &cli,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )?;

    let mut tray = TrayIconBuilder::with_id("main")
        .tooltip("WireSpot")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => crate::show_main_window(app),
            "toggle" => {
                let action = app.state::<Tray>().toggle_action.lock().unwrap().clone();
                run_action(app, &action);
            }
            "pause15" | "copy_pw" | "cli" => run_action(app, event.id.as_ref()),
            "quit" => crate::quit(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                crate::toggle_main_window(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    app.manage(Tray {
        status,
        toggle,
        pause,
        icons: Mutex::new(HashMap::new()),
        shown: Mutex::new(String::new()),
        toggle_action: Mutex::new("golive".into()),
    });
    Ok(())
}

fn run_action(app: &AppHandle, id: &str) {
    if id.is_empty() {
        return;
    }
    app.state::<crate::engine::Engine>().notify("action", json!({ "id": id }));
}

/// The engine made one .ico per state; load them once.
pub fn set_icons(app: &AppHandle, hello: &Value) {
    let Some(files) = hello.get("icons").and_then(Value::as_object) else { return };
    let Some(tray) = app.try_state::<Tray>() else { return };
    let mut icons = tray.icons.lock().unwrap();
    for (state, path) in files {
        if let Some(image) = path.as_str().and_then(|p| Image::from_path(p).ok()) {
            icons.insert(state.clone(), image.to_owned());
        }
    }
}

/// New snapshot: icon, tooltip and which actions make sense.
pub fn update(app: &AppHandle, snap: &Value) {
    let state = snap.get("icon_state").and_then(Value::as_str).unwrap_or("unknown");
    let tip = snap.get("tooltip").and_then(Value::as_str).unwrap_or("WireSpot");
    let busy = snap.get("busy").map(|b| !b.is_null() && b != "").unwrap_or(false);
    let Some(tray) = app.try_state::<Tray>() else { return };
    if let Some(icon) = app.tray_by_id("main") {
        let _ = icon.set_tooltip(Some(tip));
        let mut shown = tray.shown.lock().unwrap();
        if *shown != state {
            let key = if state == "unknown" { "idle" } else { state };
            if let Some(image) = tray.icons.lock().unwrap().get(key) {
                let _ = icon.set_icon(Some(image.clone()));
                *shown = state.to_string();
            }
        }
    }
    let _ = tray.status.set_text(tip);
    let (label, action) = match state {
        "live" | "vpn" | "error" => ("Disconnect", "disconnect"),
        "paused" => ("Resume now", "resume"),
        _ => ("Go live", "golive"),
    };
    let _ = tray.toggle.set_text(label);
    let _ = tray.toggle.set_enabled(!busy && state != "unknown");
    *tray.toggle_action.lock().unwrap() = action.to_string();
    let _ = tray.pause.set_enabled(!busy && matches!(state, "live" | "vpn"));
}
