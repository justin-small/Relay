//! The tray / menu-bar icon: status at a glance and the actions an operator needs without opening the panel.

use tauri::{
    image::Image,
    menu::{CheckMenuItem, CheckMenuItemBuilder, Menu, MenuBuilder, MenuItem, MenuItemBuilder, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, Wry,
};

use crate::backend::{Snapshot, Status};

pub struct Tray {
    icon: TrayIcon,
    status: MenuItem<Wry>,
    panel: MenuItem<Wry>,
    link: MenuItem<Wry>,
    fingerprint: MenuItem<Wry>,
    updates: MenuItem<Wry>,
    pub autostart: CheckMenuItem<Wry>,
    base: Image<'static>,
}

pub fn label(status: Status) -> &'static str {
    match status {
        Status::Stopped => "Stopped",
        Status::Starting => "Starting",
        Status::Running => "Running",
        Status::Capturing => "Capturing",
        Status::Error => "Error",
    }
}

fn colour(status: Status) -> [u8; 3] {
    match status {
        Status::Stopped => [0x8a, 0x8a, 0x85],
        Status::Starting => [0xe0, 0xa1, 0x1b],
        Status::Running => [0x2e, 0xa0, 0x5a],
        Status::Capturing => [0x2f, 0x7d, 0xe1],
        Status::Error => [0xd2, 0x3f, 0x31],
    }
}

/// The app icon with a status-coloured dot in its corner, so the state reads from the tray alone.
fn with_dot(base: &Image<'_>, status: Status) -> Image<'static> {
    let (w, h) = (base.width() as i32, base.height() as i32);
    let mut px = base.rgba().to_vec();
    let r = (w.min(h) / 4).max(3);
    let (cx, cy) = (w - r - 1, h - r - 1);
    let c = colour(status);
    for y in 0..h {
        for x in 0..w {
            let d2 = (x - cx).pow(2) + (y - cy).pow(2);
            let i = ((y * w + x) * 4) as usize;
            if d2 <= (r + 2).pow(2) && d2 > r * r {
                px[i..i + 4].copy_from_slice(&[0xff, 0xff, 0xff, 0xff]); // a light ring keeps it visible on any bar
            } else if d2 <= r * r {
                px[i..i + 4].copy_from_slice(&[c[0], c[1], c[2], 0xff]);
            }
        }
    }
    Image::new_owned(px, w as u32, h as u32)
}

pub fn build(app: &AppHandle, autostart_on: bool) -> tauri::Result<Tray> {
    let status = MenuItemBuilder::with_id("status", "Relay: starting").enabled(false).build(app)?;
    let panel = MenuItemBuilder::with_id("open_panel", "Open panel").build(app)?;
    let browser = MenuItemBuilder::with_id("open_browser", "Open panel in browser (for the firmware flasher)").build(app)?;
    let link = MenuItemBuilder::with_id("copy_link", "Copy viewer link").build(app)?;
    let fingerprint = MenuItemBuilder::with_id("fingerprint", "Show certificate fingerprint").build(app)?;
    let logs = MenuItemBuilder::with_id("open_logs", "Open logs folder").build(app)?;
    let state = MenuItemBuilder::with_id("open_state", "Open state folder").build(app)?;
    let restart = MenuItemBuilder::with_id("restart", "Restart Relay").build(app)?;
    let updates = MenuItemBuilder::with_id("updates", "Check for updates").build(app)?;
    let autostart = CheckMenuItemBuilder::with_id("autostart", "Start at login").checked(autostart_on).build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "Quit Relay").build(app)?;
    let sep = |a: &AppHandle| PredefinedMenuItem::separator(a);
    let menu: Menu<Wry> = MenuBuilder::new(app)
        .items(&[&status, &sep(app)?, &panel, &browser, &link, &fingerprint, &sep(app)?, &logs, &state, &sep(app)?, &restart, &updates, &autostart, &sep(app)?, &quit])
        .build()?;

    let base = app.default_window_icon().cloned().map(|i| i.to_owned()).expect("app icon");
    let mut builder = TrayIconBuilder::with_id("relay")
        .icon(with_dot(&base, Status::Starting))
        .tooltip("Relay: starting")
        .menu(&menu)
        .on_menu_event(|app, event| crate::on_menu(app, event.id().as_ref()))
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                crate::open_panel(tray.app_handle());
            }
        });
    // On macOS a click on the menu-bar icon opens the menu; elsewhere a left click opens the panel.
    builder = builder.show_menu_on_left_click(cfg!(target_os = "macos"));
    let icon = builder.build(app)?;
    Ok(Tray { icon, status, panel, link, fingerprint, updates, autostart, base })
}

impl Tray {
    /// The "Check for updates" item doubles as the install button once something has been found.
    pub fn set_update(&self, st: &crate::updater::State) {
        let (text, enabled) = match st.kind {
            "checking" => ("Checking for updates...".to_string(), false),
            "available" => (format!("Install Relay {} and restart...", st.version), true),
            "downloading" => (format!("Downloading Relay {}: {}%", st.version, st.percent), false),
            "installing" => ("Installing update...".to_string(), false),
            "failed" => ("Update check failed. Check again".to_string(), true),
            _ => ("Check for updates".to_string(), true),
        };
        let _ = self.updates.set_text(text);
        let _ = self.updates.set_enabled(enabled);
    }

    pub fn update(&self, snap: &Snapshot) {
        let mut text = format!("Relay: {}", label(snap.status).to_lowercase());
        if !snap.message.is_empty() {
            text = format!("{text}. {}", snap.message);
        }
        let _ = self.status.set_text(&text);
        let _ = self.icon.set_icon(Some(with_dot(&self.base, snap.status)));
        let _ = self.icon.set_tooltip(Some(&text));
        let live = matches!(snap.status, Status::Running | Status::Capturing);
        for item in [&self.panel, &self.link, &self.fingerprint] {
            let _ = item.set_enabled(live);
        }
    }
}

pub fn get(app: &AppHandle) -> Option<tauri::State<'_, Tray>> {
    app.try_state::<Tray>()
}
