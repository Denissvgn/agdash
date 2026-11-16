//! A minimal HTTP/1.1 GET to agdash's own service on loopback, with no other dependency.

use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::time::Duration;

/// GET `path` from the service on 127.0.0.1:`port`, with the local token when given.
pub fn get(
    port: u16,
    path: &str,
    token: Option<&str>,
    timeout: Duration,
) -> io::Result<(u16, String)> {
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let mut stream = TcpStream::connect_timeout(&address, timeout)?;
    stream.set_read_timeout(Some(timeout))?;
    stream.set_write_timeout(Some(timeout))?;
    let authorization = token
        .map(|value| format!("Authorization: Bearer {value}\r\n"))
        .unwrap_or_default();
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: application/json\r\n{authorization}Connection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes())?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw)?;
    parse_response(&raw)
}

fn invalid(what: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, what.to_string())
}

/// The status and body of a whole HTTP/1.1 response, chunked or not.
pub fn parse_response(raw: &[u8]) -> io::Result<(u16, String)> {
    let split = raw
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or_else(|| invalid("no end of headers"))?;
    let head = std::str::from_utf8(&raw[..split]).map_err(|_| invalid("headers are not text"))?;
    let mut lines = head.split("\r\n");
    let status = lines
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or_else(|| invalid("no status"))?;
    let chunked = lines.any(|line| {
        let lower = line.to_ascii_lowercase();
        lower.starts_with("transfer-encoding:") && lower.contains("chunked")
    });
    let body = &raw[split + 4..];
    let body = if chunked {
        dechunk(body)?
    } else {
        body.to_vec()
    };
    let text = String::from_utf8(body).map_err(|_| invalid("body is not text"))?;
    Ok((status, text))
}

fn dechunk(mut rest: &[u8]) -> io::Result<Vec<u8>> {
    let mut out = Vec::new();
    loop {
        let end = rest
            .windows(2)
            .position(|window| window == b"\r\n")
            .ok_or_else(|| invalid("bad chunk"))?;
        let size_text = std::str::from_utf8(&rest[..end]).map_err(|_| invalid("bad chunk size"))?;
        let size = usize::from_str_radix(size_text.split(';').next().unwrap_or("").trim(), 16)
            .map_err(|_| invalid("bad chunk size"))?;
        rest = &rest[end + 2..];
        if size == 0 {
            return Ok(out);
        }
        if rest.len() < size + 2 {
            return Err(invalid("short chunk"));
        }
        out.extend_from_slice(&rest[..size]);
        rest = &rest[size + 2..];
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_a_plain_response() {
        let raw = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 15\r\n\r\n{\"status\":\"ok\"}";
        assert_eq!(
            parse_response(raw).unwrap(),
            (200, "{\"status\":\"ok\"}".to_string())
        );
    }

    #[test]
    fn reads_a_chunked_response() {
        let raw = b"HTTP/1.1 401 Unauthorized\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n";
        assert_eq!(
            parse_response(raw).unwrap(),
            (401, "hello world".to_string())
        );
    }

    #[test]
    fn refuses_a_cut_response() {
        assert!(parse_response(b"HTTP/1.1 200 OK\r\ncontent-length: 3").is_err());
        assert!(
            parse_response(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nff\r\nab")
                .is_err()
        );
    }

    #[test]
    fn a_closed_port_is_an_error_not_a_hang() {
        let unused = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = unused.local_addr().unwrap().port();
        drop(unused);
        assert!(get(port, "/health", None, Duration::from_millis(500)).is_err());
    }
}
