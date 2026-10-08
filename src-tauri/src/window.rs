//! Work-area geometry for companion placement (Sprint 1).
//!
//! The companion window starts hidden (`visible: false` in tauri.conf.json)
//! and must appear fully inside the primary display's *work area* — the
//! monitor rectangle minus the taskbar — the first time it is shown.
//!
//! Only one Win32 function is used, `SystemParametersInfoW` with
//! `SPI_GETWORKAREA`, declared here as raw FFI so the host gains **no new
//! dependencies** (and no new capability permissions). The FFI is a thin
//! edge; all placement math is pure and unit-tested below.

use tauri::{PhysicalPosition, WebviewWindow};

/// Gap kept between the companion and the work-area edges, in logical
/// pixels (scaled to physical pixels per display before use).
pub const EDGE_MARGIN: i32 = 16;

/// Usable display rectangle (monitor minus taskbar), physical pixels.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WorkArea {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

impl WorkArea {
    pub fn width(&self) -> i32 {
        (self.right - self.left).max(0)
    }

    pub fn height(&self) -> i32 {
        (self.bottom - self.top).max(0)
    }
}

/// Origin that puts a `width`×`height` window against the bottom-right of
/// `area`, `margin` away from the edges, clamped so the whole window stays
/// visible even when it is larger than the work area.
pub fn bottom_right_origin(area: &WorkArea, width: u32, height: u32, margin: i32) -> (i32, i32) {
    let width = i32::try_from(width).unwrap_or(i32::MAX).min(area.width());
    let height = i32::try_from(height)
        .unwrap_or(i32::MAX)
        .min(area.height());
    let max_x = (area.right - width).max(area.left);
    let max_y = (area.bottom - height).max(area.top);
    let x = (area.right - width - margin).clamp(area.left, max_x);
    let y = (area.bottom - height - margin).clamp(area.top, max_y);
    (x, y)
}

/// Clamp an arbitrary origin so a `width`×`height` window is fully contained
/// in `area`. Used after window movement/resizing to avoid off-screen
/// positions on scaled or multi-display setups.
pub fn clamp_origin(area: &WorkArea, x: i32, y: i32, width: u32, height: u32) -> (i32, i32) {
    let width = i32::try_from(width).unwrap_or(i32::MAX).min(area.width());
    let height = i32::try_from(height)
        .unwrap_or(i32::MAX)
        .min(area.height());
    let max_x = (area.right - width).max(area.left);
    let max_y = (area.bottom - height).max(area.top);
    (x.clamp(area.left, max_x), y.clamp(area.top, max_y))
}

/// Position the (still hidden) companion inside the primary work area.
/// Never panics: failures are logged and the window keeps whatever position
/// the OS gave it, then the caller shows it regardless.
pub fn place_companion(window: &WebviewWindow) {
    let Some(area) = primary_work_area() else {
        log::warn!("work area unavailable; leaving companion at its default position");
        return;
    };
    let size = match window.outer_size() {
        Ok(size) => size,
        Err(err) => {
            log::warn!("window size unavailable ({err}); skipping initial placement");
            return;
        }
    };
    let scale = window.scale_factor().unwrap_or(1.0);
    let margin = (f64::from(EDGE_MARGIN) * scale).round() as i32;
    let (x, y) = bottom_right_origin(&area, size.width, size.height, margin);
    if let Err(err) = window.set_position(PhysicalPosition::new(x, y)) {
        log::warn!("could not position companion window: {err}");
    }
}

#[cfg(windows)]
fn primary_work_area() -> Option<WorkArea> {
    /// Win32 `RECT` as used by `SystemParametersInfoW(SPI_GETWORKAREA)`.
    #[repr(C)]
    #[derive(Default)]
    struct WinRect {
        left: i32,
        top: i32,
        right: i32,
        bottom: i32,
    }

    // SAFETY: `SystemParametersInfoW` is linked from user32.dll (always
    // loaded in a GUI process). `pv_param` receives a caller-owned RECT for
    // this query; no pointers are retained by the callee. `ui_action` is the
    // documented constant `SPI_GETWORKAREA` and `ui_param`/`f_win_ini` are 0
    // for a read-only query.
    #[link(name = "user32")]
    extern "system" {
        fn SystemParametersInfoW(
            ui_action: u32,
            ui_param: u32,
            pv_param: *mut std::ffi::c_void,
            f_win_ini: u32,
        ) -> i32;
    }

    /// `SPI_GETWORKAREA`: retrieves the work area of the primary display.
    const SPI_GETWORKAREA: u32 = 48;

    let mut rect = WinRect::default();
    let ok = unsafe {
        SystemParametersInfoW(
            SPI_GETWORKAREA,
            0,
            (&mut rect as *mut WinRect).cast::<std::ffi::c_void>(),
            0,
        )
    };
    if ok == 0 {
        return None;
    }
    let area = WorkArea {
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
    };
    if area.width() == 0 || area.height() == 0 {
        None
    } else {
        Some(area)
    }
}

#[cfg(not(windows))]
fn primary_work_area() -> Option<WorkArea> {
    // Dude targets Windows; other platforms keep the default window
    // position rather than guessing at a work area.
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn area() -> WorkArea {
        WorkArea {
            left: 0,
            top: 0,
            right: 1920,
            bottom: 1040, // 1080p minus a 40 px taskbar
        }
    }

    #[test]
    fn bottom_right_puts_window_above_taskbar_with_margin() {
        let (x, y) = bottom_right_origin(&area(), 408, 114, 16);
        assert_eq!(x, 1920 - 408 - 16);
        assert_eq!(y, 1040 - 114 - 16);
    }

    #[test]
    fn origin_is_clamped_inside_when_window_exceeds_work_area() {
        let (x, y) = bottom_right_origin(&area(), 2400, 1400, 16);
        assert_eq!((x, y), (0, 0));
    }

    #[test]
    fn negative_margin_never_escapes_the_area() {
        // A margin that would push the window past the edge is clamped so
        // the window still sits fully inside, flush with the bottom-right.
        let (x, y) = bottom_right_origin(&area(), 100, 100, -9999);
        assert_eq!((x, y), (1920 - 100, 1040 - 100));
    }

    #[test]
    fn clamp_keeps_visible_window_inside() {
        // Fully inside: unchanged.
        assert_eq!(clamp_origin(&area(), 100, 200, 408, 114), (100, 200));
        // Hanging off the bottom-right: pulled back in.
        assert_eq!(
            clamp_origin(&area(), 1900, 1030, 408, 114),
            (1920 - 408, 1040 - 114)
        );
        // Off the top-left: pulled back in.
        assert_eq!(clamp_origin(&area(), -500, -500, 408, 114), (0, 0));
    }

    #[test]
    fn clamp_handles_window_larger_than_work_area() {
        assert_eq!(clamp_origin(&area(), 50, 50, 4000, 3000), (0, 0));
    }

    #[test]
    fn clamp_handles_empty_or_inverted_area_gracefully() {
        let empty = WorkArea {
            left: 10,
            top: 10,
            right: 10,
            bottom: 10,
        };
        assert_eq!(empty.width(), 0);
        assert_eq!(empty.height(), 0);
        let (x, y) = clamp_origin(&empty, 100, 100, 10, 10);
        assert_eq!((x, y), (10, 10));
    }
}
