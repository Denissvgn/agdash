//! How many sessions the live board shows as live, counted the way the board counts them.
//!
//! The board shows active, waiting and idle sessions, and unknown ones that were
//! active in the last 30 minutes. The tray asks the service the same two
//! questions and counts each session once.

use std::collections::BTreeSet;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::Value;

/// The board's window for an unknown session to count as live.
pub const UNKNOWN_WINDOW: Duration = Duration::from_secs(30 * 60);
const LIMIT: u32 = 200;

/// The two requests, as paths with their query strings.
pub fn queries(now: SystemTime) -> [String; 2] {
    let since = iso(now.checked_sub(UNKNOWN_WINDOW).unwrap_or(UNIX_EPOCH));
    [
        format!("/api/sessions?state=active&state=waiting&state=idle&limit={LIMIT}"),
        format!("/api/sessions?state=unknown&active_since={since}&limit={LIMIT}"),
    ]
}

/// Sessions on either page, each counted once.
pub fn count(pages: &[&str]) -> Result<usize, String> {
    let mut ids = BTreeSet::new();
    for page in pages {
        let parsed: Value = serde_json::from_str(page).map_err(|error| error.to_string())?;
        let items = parsed
            .get("items")
            .and_then(Value::as_array)
            .ok_or_else(|| "the answer has no items".to_string())?;
        for item in items {
            if let Some(id) = item.get("id").and_then(Value::as_i64) {
                ids.insert(id);
            }
        }
    }
    Ok(ids.len())
}

/// What the tray shows next to its icon.
pub fn title(count: Option<usize>) -> String {
    match count {
        None => "–".to_string(),
        Some(count) => count.to_string(),
    }
}

/// The tray's tooltip, in words.
pub fn tooltip(count: Option<usize>) -> String {
    match count {
        None => "agdash: the service is not answering".to_string(),
        Some(0) => "agdash: nothing live".to_string(),
        Some(1) => "agdash: 1 live session".to_string(),
        Some(count) => format!("agdash: {count} live sessions"),
    }
}

/// An instant as `YYYY-MM-DDTHH:MM:SSZ`, in UTC.
pub fn iso(moment: SystemTime) -> String {
    let seconds = moment
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let (days, rest) = ((seconds / 86_400) as i64, seconds % 86_400);
    // Howard Hinnant's days-to-civil conversion.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rest / 3_600,
        rest % 3_600 / 60,
        rest % 60
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_session_counts_once_across_both_pages() {
        let live = r#"{"items":[{"id":1,"state":"active"},{"id":2,"state":"waiting"}],"next_cursor":null}"#;
        let unknown = r#"{"items":[{"id":3,"state":"unknown"},{"id":2,"state":"waiting"}],"next_cursor":null}"#;
        assert_eq!(count(&[live, unknown]), Ok(3));
        assert_eq!(count(&[r#"{"items":[]}"#]), Ok(0));
        assert!(count(&["not json"]).is_err());
        assert!(count(&[r#"{"detail":"the local token is missing or wrong"}"#]).is_err());
    }

    #[test]
    fn the_questions_match_the_board() {
        let now = UNIX_EPOCH + Duration::from_secs(1_794_830_400); // 2026-11-16T12:00:00Z
        let [live, unknown] = queries(now);
        assert_eq!(
            live,
            "/api/sessions?state=active&state=waiting&state=idle&limit=200"
        );
        assert_eq!(
            unknown,
            "/api/sessions?state=unknown&active_since=2026-11-16T11:30:00Z&limit=200"
        );
    }

    #[test]
    fn instants_read_as_utc() {
        assert_eq!(iso(UNIX_EPOCH), "1970-01-01T00:00:00Z");
        assert_eq!(
            iso(UNIX_EPOCH + Duration::from_secs(951_782_400)),
            "2000-02-29T00:00:00Z"
        );
        assert_eq!(
            iso(UNIX_EPOCH + Duration::from_secs(1_794_830_400)),
            "2026-11-16T12:00:00Z"
        );
    }

    #[test]
    fn the_tray_says_it_in_words() {
        assert_eq!(
            (title(Some(3)), tooltip(Some(3))),
            ("3".to_string(), "agdash: 3 live sessions".to_string())
        );
        assert_eq!(tooltip(Some(1)), "agdash: 1 live session");
        assert_eq!(tooltip(Some(0)), "agdash: nothing live");
        assert_eq!(
            (title(None), tooltip(None)),
            (
                "–".to_string(),
                "agdash: the service is not answering".to_string()
            )
        );
    }
}
