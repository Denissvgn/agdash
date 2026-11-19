//! The agdash desktop app's logic, apart from the window and tray so it can be tested.
//!
//! The app talks to agdash's own service, on loopback, and never to an agent or a
//! harness. The one request it makes elsewhere is the update check, when the person
//! asks for it from the tray. Quitting the app leaves the service running.

pub mod http;
pub mod live;
pub mod service;
pub mod update;
