fn main() {
    // WireSpot changes network settings, so the release app always runs as
    // administrator (Windows asks once, with UAC). Debug builds run as the
    // current user, so the interface can be worked on without elevation.
    let release = std::env::var("PROFILE").map(|p| p == "release").unwrap_or(false);
    let mut windows = tauri_build::WindowsAttributes::new();
    if release {
        windows = windows.app_manifest(include_str!("wirespot.manifest"));
    }

    // The release app carries its engine and CLI (the PyInstaller builds that
    // build.bat copies into payload/), so WireSpot.exe is a single download.
    // See payload.rs for how they're unpacked and checked.
    println!("cargo::rustc-check-cfg=cfg(embedded_payload)");
    for name in ["WireSpotEngine.exe", "WireSpotCLI.exe"] {
        println!("cargo:rerun-if-changed=payload/{}", name);
    }
    let payload = std::path::Path::new("payload");
    if release && payload.join("WireSpotEngine.exe").is_file() && payload.join("WireSpotCLI.exe").is_file() {
        println!("cargo:rustc-cfg=embedded_payload");
    }

    tauri_build::try_build(tauri_build::Attributes::new().windows_attributes(windows))
        .expect("failed to run the Tauri build script");
}
