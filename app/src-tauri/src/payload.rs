//! The engine and CLI inside the release WireSpot.exe (see build.rs).
//!
//! WireSpot runs as administrator, and so does the engine it starts. The
//! engine's file sits in a folder your user account can write to, so before
//! it's started the app checks that the file is exactly the copy it carries
//! (rewriting it if not), then keeps it open without sharing write or delete
//! access for as long as WireSpot runs. Nothing can swap it for another
//! program to get administrator rights.

use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const ENGINE: &str = "WireSpotEngine.exe";
#[cfg_attr(not(embedded_payload), allow(dead_code))]
pub const CLI: &str = "WireSpotCLI.exe";

#[cfg(embedded_payload)]
const FILES: &[(&str, &[u8])] = &[
    (ENGINE, include_bytes!("../payload/WireSpotEngine.exe")),
    (CLI, include_bytes!("../payload/WireSpotCLI.exe")),
];
#[cfg(not(embedded_payload))]
const FILES: &[(&str, &[u8])] = &[];

/// Checked files, held open so they can't change while WireSpot runs.
static HELD: Mutex<Option<HashMap<PathBuf, File>>> = Mutex::new(None);

/// This build carries its engine (release builds made by build.bat).
pub fn embedded() -> bool {
    !FILES.is_empty()
}

/// Makes sure `dir` holds exactly the engine and CLI this build carries, and
/// keeps them locked. Only the engine is required; the CLI is best effort
/// (it may be open in a terminal).
pub fn materialize(dir: &Path) -> Result<(), String> {
    std::fs::create_dir_all(dir).map_err(|e| format!("Couldn't create {}: {}", dir.display(), e))?;
    let mut held = HELD.lock().unwrap();
    let held = held.get_or_insert_with(HashMap::new);
    for (name, bytes) in FILES {
        let path = dir.join(name);
        if held.contains_key(&path) {
            continue; // checked earlier and locked since
        }
        match checked(&path, bytes) {
            Ok(file) => {
                held.insert(path, file);
            }
            Err(e) if *name == ENGINE => return Err(e),
            Err(_) => {}
        }
    }
    Ok(())
}

/// The file at `path`, verified to hold `bytes` and opened without write/delete sharing.
fn checked(path: &Path, bytes: &[u8]) -> Result<File, String> {
    if let Ok(mut file) = open_locked(path) {
        if same_contents(&mut file, bytes) {
            return Ok(file);
        }
    }
    // missing or different: write a fresh copy next to it, then swap it in
    let fresh = path.with_extension(format!("new-{}", std::process::id()));
    std::fs::write(&fresh, bytes).map_err(|e| format!("Couldn't unpack {}: {}", fresh.display(), e))?;
    if let Err(e) = std::fs::rename(&fresh, path) {
        let _ = std::fs::remove_file(&fresh);
        return Err(format!("Couldn't update {} (is it still running?): {}", path.display(), e));
    }
    let mut file = open_locked(path).map_err(|e| format!("Couldn't open {}: {}", path.display(), e))?;
    if !same_contents(&mut file, bytes) {
        return Err(format!("{} was changed while it was being unpacked.", path.display()));
    }
    Ok(file)
}

fn open_locked(path: &Path) -> std::io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.share_mode(0x1); // FILE_SHARE_READ: others may run it, not change, rename or delete it
    }
    options.open(path)
}

fn same_contents(file: &mut File, bytes: &[u8]) -> bool {
    if file.metadata().map(|m| m.len() != bytes.len() as u64).unwrap_or(true) {
        return false;
    }
    let mut buf = vec![0u8; 1 << 20];
    let mut offset = 0;
    loop {
        match file.read(&mut buf) {
            Ok(0) => return offset == bytes.len(),
            Ok(n) => {
                if offset + n > bytes.len() || buf[..n] != bytes[offset..offset + n] {
                    return false;
                }
                offset += n;
            }
            Err(_) => return false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("wirespot-payload-{}-{}", name, std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn unpacks_a_missing_file() {
        let path = temp_dir("missing").join("engine.exe");
        let file = checked(&path, b"MZ engine").unwrap();
        drop(file);
        assert_eq!(std::fs::read(&path).unwrap(), b"MZ engine");
    }

    #[test]
    fn replaces_a_file_that_was_changed() {
        let path = temp_dir("changed").join("engine.exe");
        std::fs::write(&path, b"MZ something else").unwrap();
        drop(checked(&path, b"MZ engine").unwrap());
        assert_eq!(std::fs::read(&path).unwrap(), b"MZ engine");
    }

    #[test]
    fn a_checked_file_cannot_be_changed_or_removed_while_held() {
        let path = temp_dir("held").join("engine.exe");
        let held = checked(&path, b"MZ engine").unwrap();
        assert!(OpenOptions::new().write(true).open(&path).is_err(), "writing must be refused");
        assert!(std::fs::remove_file(&path).is_err(), "deleting must be refused");
        assert!(std::fs::rename(&path, path.with_extension("old")).is_err(), "renaming must be refused");
        assert_eq!(std::fs::read(&path).unwrap(), b"MZ engine", "reading (and running) still works");
        drop(held);
        assert!(std::fs::remove_file(&path).is_ok());
    }
}
