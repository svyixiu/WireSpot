//! Pop-ups while WireSpot is in the tray: a small card in the bottom-right
//! corner for devices waiting for approval (with Allow / Block) and for the
//! engine's messages. It never takes focus, sizes itself to its cards, and
//! hides when there's nothing left or the main window opens (which shows the
//! same things itself).

use serde_json::{json, Value};
use std::collections::VecDeque;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{AppHandle, LogicalPosition, LogicalSize, Manager, WebviewUrl, WebviewWindowBuilder};

const LABEL: &str = "notify";
const WIDTH: f64 = 380.0;
const MARGIN: f64 = 12.0;
/// how long a message is still worth showing to a window that opens late
const FRESH: Duration = Duration::from_secs(8);

/// Recent messages, for a pop-up window that is still loading when they arrive.
static RECENT: Mutex<VecDeque<(Instant, Value)>> = Mutex::new(VecDeque::new());

/// An engine message worth a pop-up while the window is hidden.
pub fn message(app: &AppHandle, event: &str, data: &Value) {
    let item = json!({ "event": event, "data": data });
    {
        let mut recent = RECENT.lock().unwrap();
        recent.retain(|(at, _)| at.elapsed() < FRESH);
        recent.push_back((Instant::now(), item));
        while recent.len() > 4 {
            recent.pop_front();
        }
    }
    show(app);
}

/// Shows the pop-up window (creating it the first time), unless the main window is open.
pub fn show(app: &AppHandle) {
    if crate::main_window_visible(app) {
        return;
    }
    if let Some(window) = app.get_webview_window(LABEL) {
        let _ = window.show();
        return;
    }
    let built = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("notify.html".into()))
        .title("WireSpot")
        .inner_size(WIDTH, 160.0)
        .resizable(false)
        .maximizable(false)
        .minimizable(false)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .skip_taskbar(true)
        .always_on_top(true)
        // never activates: its buttons work, but whatever you're typing in keeps the focus
        .focusable(false)
        .focused(false)
        .visible(false) // shown once it has measured its cards (notify_resize)
        .build();
    if let Ok(window) = built {
        place(&window, 160.0);
    }
}

pub fn hide(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(LABEL) {
        let _ = window.hide();
    }
}

/// Bottom-right of the work area of the screen with the taskbar.
fn place(window: &tauri::WebviewWindow, height: f64) {
    let Ok(Some(monitor)) = window.primary_monitor() else { return };
    let scale = monitor.scale_factor();
    let area = monitor.work_area();
    let right = (area.position.x as f64 + area.size.width as f64) / scale;
    let bottom = (area.position.y as f64 + area.size.height as f64) / scale;
    let _ = window.set_size(LogicalSize::new(WIDTH, height));
    let _ = window.set_position(LogicalPosition::new(right - WIDTH - MARGIN, bottom - height - MARGIN));
}

/// What the pop-up shows when it opens: waiting devices and recent messages.
#[tauri::command]
pub fn notify_state(app: AppHandle) -> Value {
    let snap = app.state::<crate::engine::Engine>().snap.lock().unwrap().clone();
    let pending = snap.as_ref().and_then(|s| s.get("pending").cloned()).unwrap_or_else(|| json!([]));
    let mut recent = RECENT.lock().unwrap();
    recent.retain(|(at, _)| at.elapsed() < FRESH);
    json!({ "pending": pending, "messages": recent.iter().map(|(_, m)| m.clone()).collect::<Vec<_>>() })
}

/// The cards changed: fit the window to them, or hide it when there are none.
#[tauri::command]
pub fn notify_resize(app: AppHandle, height: f64) {
    let Some(window) = app.get_webview_window(LABEL) else { return };
    if height < 1.0 || crate::main_window_visible(&app) {
        let _ = window.hide();
        return;
    }
    place(&window, height.min(640.0));
    let _ = window.show();
}
