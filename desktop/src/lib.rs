//! The agdash desktop app's logic, apart from the window and tray so it can be tested.
//!
//! The app only ever talks to agdash's own service, on loopback, and never to an
//! agent or a harness. Quitting the app leaves the service running.

pub mod http;
pub mod live;
pub mod service;
