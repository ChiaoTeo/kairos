//! Live Binance market smoke test.
//!
//! This test intentionally talks to the real Binance public WebSocket and is
//! ignored by default. Run it explicitly with `--ignored --nocapture`.

use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tempfile::TempDir;

struct MarketServer {
    child: Child,
    workspace: TempDir,
    control_socket: PathBuf,
    event_socket: PathBuf,
}

impl MarketServer {
    fn start() -> Self {
        let workspace = tempfile::Builder::new()
            .prefix("km")
            .tempdir_in("/tmp")
            .expect("create smoke-test workspace");
        std::fs::write(
            workspace.path().join("kairos.toml"),
            "version = 1\nworkspace_id = \"market-smoke\"\n",
        )
        .expect("write smoke-test workspace manifest");

        let control_socket = workspace.path().join("run/market/market.sock");
        let event_socket = workspace
            .path()
            .join("run/market-events/market-events.sock");
        let child = Command::new(env!("CARGO_BIN_EXE_kairos-market-server"))
            .args([
                "--workspace",
                workspace.path().to_str().expect("workspace path is utf-8"),
                "--provider",
                "binance-spot-websocket",
            ])
            .spawn()
            .expect("start kairos-market-server");

        wait_for_socket(&control_socket);
        wait_for_socket(&event_socket);
        Self {
            child,
            workspace,
            control_socket,
            event_socket,
        }
    }

    fn subscribe_btcusdt(&self) -> Value {
        let request = json!({
            "schema_version": 1,
            "command_id": "market-smoke-1",
            "idempotency_key": "market-smoke-1",
            "operation": "market.subscribe",
            "strategy_id": "market-smoke",
            "instance_id": "default",
            "payload": {
                "subject": "market.BTCUSDT",
                "selectors": ["quote"],
                "exchange": null,
                "market_type": null,
                "asset_type": null,
                "identity": null,
                "dynamic": false
            }
        });
        let response = http_request(&self.control_socket, "POST", "/v1/subscribe", &request);
        assert!(
            response.starts_with("HTTP/1.1 202"),
            "subscribe response: {response}"
        );
        parse_http_json(&response)
    }

    fn connect_events(&self) -> UnixStream {
        let stream = UnixStream::connect(&self.event_socket).expect("connect market events");
        stream
            .set_read_timeout(Some(Duration::from_secs(30)))
            .expect("set event read timeout");
        stream
    }

    fn next_quote(&self, mut stream: UnixStream) -> Vec<u8> {
        let mut seen = Vec::new();
        for _ in 0..100 {
            let mut frame_length = [0_u8; 4];
            stream
                .read_exact(&mut frame_length)
                .expect("read market event frame length");
            let length = u32::from_be_bytes(frame_length) as usize;
            assert!(length > 8 && length < 4 * 1024 * 1024);

            let mut payload = vec![0_u8; length];
            stream
                .read_exact(&mut payload)
                .expect("read market event payload");
            seen.push(String::from_utf8_lossy(&payload[4..8]).into_owned());
            if &payload[4..8] == b"MQT1" {
                return payload;
            }
        }
        panic!("did not receive a quote event after reading 100 market events: {seen:?}");
    }
}

impl Drop for MarketServer {
    fn drop(&mut self) {
        let _ = http_request(&self.control_socket, "POST", "/v1/stop", &json!({}));
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = &self.workspace;
    }
}

#[test]
#[ignore = "requires live Binance public WebSocket access"]
fn market_process_subscribes_and_receives_binance_quote() {
    let server = MarketServer::start();
    let events = server.connect_events();
    let response = server.subscribe_btcusdt();
    assert_eq!(response["status"], "accepted");

    let payload = server.next_quote(events);
    println!("received Binance quote event: {} bytes", payload.len());
}

fn wait_for_socket(path: &Path) {
    let deadline = Instant::now() + Duration::from_secs(30);
    while !path.exists() {
        assert!(
            Instant::now() < deadline,
            "socket did not appear: {}",
            path.display()
        );
        thread::sleep(Duration::from_millis(50));
    }
}

fn http_request(socket_path: &Path, method: &str, path: &str, body: &Value) -> String {
    let payload = serde_json::to_vec(body).expect("encode HTTP body");
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
        payload.len()
    );
    let mut stream = UnixStream::connect(socket_path).expect("connect market control");
    stream
        .write_all(request.as_bytes())
        .and_then(|_| stream.write_all(&payload))
        .expect("write market HTTP request");
    stream
        .shutdown(std::net::Shutdown::Write)
        .expect("close HTTP request");
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .expect("read market HTTP response");
    response
}

fn parse_http_json(response: &str) -> Value {
    let (_, body) = response.split_once("\r\n\r\n").expect("HTTP response body");
    serde_json::from_str(body).expect("decode HTTP JSON response")
}
