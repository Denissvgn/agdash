//! Prints the number the tray shows, asked of the running service the way the tray asks it.
//!
//!     AGDASH_BIN=/path/to/agdash cargo run --example live_count

use std::time::{Duration, SystemTime};

use agdash_desktop::{http, live, service};

fn main() {
    let port = service::port_from(std::env::var("AGDASH_PORT").ok());
    let bin = service::find_bin(std::env::var("AGDASH_BIN").ok(), None);
    let token = service::token(&bin).ok();
    let pages: Option<Vec<String>> = live::queries(SystemTime::now())
        .iter()
        .map(
            |path| match http::get(port, path, token.as_deref(), Duration::from_secs(3)) {
                Ok((200, body)) => Some(body),
                _ => None,
            },
        )
        .collect();
    let count = pages
        .and_then(|pages| live::count(&pages.iter().map(String::as_str).collect::<Vec<_>>()).ok());
    println!("{} | {}", live::title(count), live::tooltip(count));
}
