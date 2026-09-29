//! Runs the WireSpot engine (Python: wirespot/bridge.py) and relays its JSON lines.
//!
//! The engine does all the networking (WireGuard, Mobile Hotspot, device
//! approval, the guard, the sync with the CLI). This side only starts it,
//! restarts it if it crashes, matches replies to requests, and forwards its
//! events to the interface ("engine" events) and to the tray.

use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_clipboard_manager::ClipboardExt;

#[derive(Default)]
pub struct Engine {
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
    pending: Mutex<HashMap<u64, mpsc::Sender<Result<Value, String>>>>,
    next_id: AtomicU64,
    /// the engine's "ready" info: version, paths, tray icon files…
    pub hello: Mutex<Option<Value>>,
    /// the latest status snapshot
    pub snap: Mutex<Option<Value>>,
    /// what went wrong the last time it stopped (stderr tail)
    last_error: Mutex<String>,
    quitting: AtomicBool,
    /// the installer is showing: the engine runs in its light setup mode
    pub setup: AtomicBool,
    /// stopped on purpose to start again (setup -> app): no "down" event
    restarting: AtomicBool,
}

impl Engine {
    fn write(&self, msg: &Value) -> Result<(), String> {
        let mut guard = self.stdin.lock().unwrap();
        let stdin = guard.as_mut().ok_or("The WireSpot engine isn't running.")?;
        let line = format!("{}\n", msg);
        stdin
            .write_all(line.as_bytes())
            .and_then(|_| stdin.flush())
            .map_err(|e| format!("Couldn't reach the WireSpot engine: {}", e))
    }

    /// Fire-and-forget request (no reply expected).
    pub fn notify(&self, method: &str, params: Value) {
        let _ = self.write(&json!({ "method": method, "params": params }));
    }

    /// A request that waits for the engine's reply.
    pub fn call_blocking(&self, method: &str, params: Value, timeout: Duration) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed) + 1;
        let (tx, rx) = mpsc::channel();
        self.pending.lock().unwrap().insert(id, tx);
        if let Err(e) = self.write(&json!({ "id": id, "method": method, "params": params })) {
            self.pending.lock().unwrap().remove(&id);
            return Err(e);
        }
        let result = rx.recv_timeout(timeout).unwrap_or_else(|_| Err("The WireSpot engine didn't answer in time.".into()));
        self.pending.lock().unwrap().remove(&id);
        result
    }

    fn fail_pending(&self, why: &str) {
        for (_, tx) in self.pending.lock().unwrap().drain() {
            let _ = tx.send(Err(why.to_string()));
        }
    }
}

// ------------------------------------------------------------------ commands

/// Call the engine: `invoke('engine', { method, params })`.
#[tauri::command]
pub async fn engine(app: AppHandle, method: String, params: Option<Value>) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.state::<Engine>().call_blocking(&method, params.unwrap_or(json!({})), Duration::from_secs(90))
    })
    .await
    .map_err(|e| e.to_string())?
}

/// What the interface needs on start: the engine's info and latest snapshot.
#[tauri::command]
pub fn engine_state(app: AppHandle) -> Value {
    let e = app.state::<Engine>();
    json!({
        "hello": e.hello.lock().unwrap().clone(),
        "snap": e.snap.lock().unwrap().clone(),
        "running": e.stdin.lock().unwrap().is_some(),
        "error": e.last_error.lock().unwrap().clone(),
    })
}

// ------------------------------------------------------------------ process

/// Where the engine and CLI live: next to an installed WireSpot.exe, otherwise
/// (a portable run, the installer) in %LOCALAPPDATA%\WireSpot\bin\<version>.
fn program_dir(exe_dir: &Path) -> PathBuf {
    if exe_dir.join("install.json").is_file() {
        exe_dir.to_path_buf()
    } else {
        crate::app_local_dir().join("bin").join(env!("CARGO_PKG_VERSION"))
    }
}

/// The engine this build carries (checked, see payload.rs), a WireSpotEngine.exe
/// next to WireSpot.exe, or the Python sources in development.
fn engine_command() -> Result<Command, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let exe_dir = exe.parent().map(Path::to_path_buf).unwrap_or_default();
    let dir = if crate::payload::embedded() {
        let dir = program_dir(&exe_dir);
        crate::payload::materialize(&dir)?;
        Some(dir)
    } else if exe_dir.join(crate::payload::ENGINE).is_file() {
        Some(exe_dir.clone())
    } else {
        None
    };
    if let Some(dir) = dir {
        let mut cmd = Command::new(dir.join(crate::payload::ENGINE));
        // the engine finds your data from where WireSpot.exe is, and the CLI next to itself
        cmd.current_dir(&dir)
            .env("WIRESPOT_APP_DIR", &exe_dir)
            .env("WIRESPOT_BIN_DIR", &dir)
            .env("WIRESPOT_APP_EXE", &exe);
        return Ok(cmd);
    }
    // development: the repository holds the Python package (app/src-tauri/../../wirespot)
    let candidates = [
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join(".."),
        exe_dir.join("..").join("..").join("..").join(".."),
    ];
    let repo = candidates
        .iter()
        .find(|d| d.join("wirespot").join("bridge.py").exists())
        .ok_or("WireSpotEngine.exe is missing next to WireSpot.exe.")?;
    let mut cmd = Command::new("py");
    cmd.args(["-3", "-m", "wirespot.bridge"]).current_dir(repo);
    Ok(cmd)
}

fn spawn(app: &AppHandle) -> Result<(Child, std::process::ChildStdout, std::process::ChildStderr), String> {
    let mut cmd = engine_command()?;
    if app.state::<Engine>().setup.load(Ordering::Relaxed) {
        cmd.arg("--setup");
    } else if std::env::args().any(|a| a == "--autostart") {
        cmd.arg("--autostart");
    }
    cmd.env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let mut child = cmd.spawn().map_err(|e| format!("Couldn't start the WireSpot engine: {}", e))?;
    let stdin = child.stdin.take().ok_or("no stdin")?;
    let stdout = child.stdout.take().ok_or("no stdout")?;
    let stderr = child.stderr.take().ok_or("no stderr")?;
    *app.state::<Engine>().stdin.lock().unwrap() = Some(stdin);
    Ok((child, stdout, stderr))
}

/// Starts the engine and keeps it running until the app quits.
pub fn start(app: AppHandle) {
    std::thread::spawn(move || {
        let mut quick_failures = 0u32;
        loop {
            let started = Instant::now();
            match spawn(&app) {
                Ok((child, stdout, stderr)) => {
                    *app.state::<Engine>().child.lock().unwrap() = Some(child);
                    let tail = collect_stderr(stderr);
                    for line in BufReader::new(stdout).lines() {
                        match line {
                            Ok(line) if !line.trim().is_empty() => on_line(&app, &line),
                            Ok(_) => {}
                            Err(_) => break,
                        }
                    }
                    let e = app.state::<Engine>();
                    *e.stdin.lock().unwrap() = None;
                    if let Some(mut child) = e.child.lock().unwrap().take() {
                        let _ = child.wait();
                    }
                    e.fail_pending("The WireSpot engine stopped.");
                    let why = tail.lock().unwrap().iter().cloned().collect::<Vec<_>>().join("\n");
                    *e.last_error.lock().unwrap() = why.clone();
                    if e.quitting.load(Ordering::Relaxed) {
                        return;
                    }
                    if e.restarting.swap(false, Ordering::Relaxed) {
                        // stopped on purpose (see restart): start the new one right away
                        *e.hello.lock().unwrap() = None;
                        *e.snap.lock().unwrap() = None;
                        quick_failures = 0;
                        continue;
                    }
                    let _ = app.emit("engine", json!({ "event": "down", "data": { "error": why } }));
                }
                Err(err) => {
                    *app.state::<Engine>().last_error.lock().unwrap() = err.clone();
                    let _ = app.emit("engine", json!({ "event": "down", "data": { "error": err } }));
                }
            }
            if app.state::<Engine>().quitting.load(Ordering::Relaxed) {
                return;
            }
            // restart, backing off when it keeps failing right away
            quick_failures = if started.elapsed() < Duration::from_secs(20) { quick_failures + 1 } else { 0 };
            std::thread::sleep(Duration::from_secs(u64::from(quick_failures.min(10)) * 2 + 1));
        }
    });
}

fn collect_stderr(stderr: std::process::ChildStderr) -> std::sync::Arc<Mutex<VecDeque<String>>> {
    let tail = std::sync::Arc::new(Mutex::new(VecDeque::new()));
    let keep = tail.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let mut t = keep.lock().unwrap();
            t.push_back(line);
            if t.len() > 30 {
                t.pop_front();
            }
        }
    });
    tail
}

fn on_line(app: &AppHandle, line: &str) {
    let Ok(msg) = serde_json::from_str::<Value>(line) else { return };
    let engine = app.state::<Engine>();
    if let Some(id) = msg.get("id").and_then(Value::as_u64) {
        let reply = match msg.get("error") {
            Some(err) => Err(err.as_str().unwrap_or("error").to_string()),
            None => Ok(msg.get("result").cloned().unwrap_or(Value::Null)),
        };
        if let Some(tx) = engine.pending.lock().unwrap().remove(&id) {
            let _ = tx.send(reply);
        }
        return;
    }
    let Some(name) = msg.get("event").and_then(Value::as_str) else { return };
    let data = msg.get("data").cloned().unwrap_or(Value::Null);
    match name {
        "ready" => {
            *engine.hello.lock().unwrap() = Some(data.clone());
            crate::tray::set_icons(app, &data);
            // the window may already be open: let the engine poll at the fast rate
            if crate::main_window_visible(app) {
                engine.notify("visible", json!({ "visible": true }));
            }
        }
        "snap" => {
            *engine.snap.lock().unwrap() = Some(data.clone());
            crate::tray::update(app, &data);
        }
        "clipboard" => {
            if let Some(text) = data.get("text").and_then(Value::as_str) {
                let _ = app.clipboard().write_text(text.to_string());
            }
            return;
        }
        "navigate" => crate::show_main_window(app),
        // in the tray, a device asking to join and the engine's messages get a pop-up
        "pending" if !crate::main_window_visible(app) => crate::notify::show(app),
        "toast" | "notify" if !crate::main_window_visible(app) => crate::notify::message(app, name, &data),
        "quit" => {
            crate::quit(app);
            return;
        }
        _ => {}
    }
    let _ = app.emit("engine", json!({ "event": name, "data": data }));
}

/// Stops the engine and starts it again at once, e.g. in app mode after the
/// installer's "Run without installing". The interface gets a new "ready".
pub fn restart(app: &AppHandle) {
    let e = app.state::<Engine>();
    e.restarting.store(true, Ordering::Relaxed);
    e.notify("quit", json!({}));
    // an engine that doesn't answer is stopped the hard way
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(6));
        let e = app.state::<Engine>();
        if e.restarting.load(Ordering::Relaxed) {
            let child = e.child.lock().unwrap().take();
            if let Some(mut child) = child {
                let _ = child.kill();
            }
        }
    });
}

/// Asks the engine to stop (the VPN and hotspot keep running), then waits briefly.
pub fn shutdown(app: &AppHandle) {
    let e = app.state::<Engine>();
    e.quitting.store(true, Ordering::Relaxed);
    e.notify("quit", json!({}));
    let deadline = Instant::now() + Duration::from_secs(4);
    while Instant::now() < deadline {
        let done = match e.child.lock().unwrap().as_mut() {
            Some(child) => child.try_wait().map(|s| s.is_some()).unwrap_or(true),
            None => true,
        };
        if done {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let child = e.child.lock().unwrap().take();
    if let Some(mut child) = child {
        let _ = child.kill();
    }
}
