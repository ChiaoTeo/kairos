websocket_connection!(
    BinanceOptionsWebSocketConnection,
    "options.websocket",
    "options"
);
market_websocket_capabilities!(BinanceOptionsWebSocketConnection, "options");

#[cfg(test)]
mod tests {
    use crate::ConnectionKey;
    use crate::participants::binance::BinanceWebSocketConfig;
    use crate::services::participants::binance::market_stream::{PlannedStream, StreamRoute};

    use super::BinanceOptionsWebSocketConnection;

    fn connection() -> BinanceOptionsWebSocketConnection {
        BinanceOptionsWebSocketConnection::new(
            ConnectionKey::new("options-test").unwrap(),
            BinanceWebSocketConfig {
                environment: "public".into(),
                endpoint: "wss://fstream.binance.com".into(),
                credential: None,
                event_capacity: 32,
            },
        )
        .unwrap()
    }

    #[test]
    fn assigns_more_than_two_hundred_streams_to_capacity_bounded_shards() {
        let mut connection = connection();
        let streams = (0..401).map(|index| PlannedStream {
            route: StreamRoute::Public,
            name: format!("option-{index:04}@ticker"),
        });

        let assigned = connection.assign_streams(streams).unwrap();
        let counts =
            assigned
                .into_iter()
                .fold(std::collections::BTreeMap::new(), |mut counts, stream| {
                    *counts.entry(stream.shard).or_insert(0_usize) += 1;
                    counts
                });

        assert_eq!(counts.len(), 3);
        assert_eq!(counts.values().copied().collect::<Vec<_>>(), [200, 200, 1]);
    }

    #[test]
    fn repeated_stream_demand_keeps_the_same_shard() {
        let mut connection = connection();
        let stream = PlannedStream {
            route: StreamRoute::Public,
            name: "btc-260925-100000-c@ticker".into(),
        };

        let first = connection.assign_streams([stream.clone()]).unwrap();
        let second = connection.assign_streams([stream]).unwrap();

        assert_eq!(first, second);
        assert_eq!(connection.stream_assignments.len(), 1);
    }
}

user_websocket_connection!(
    BinanceOptionsUserWebSocketConnection,
    "options.user.websocket",
    "/eapi/v1/listenKey"
);
