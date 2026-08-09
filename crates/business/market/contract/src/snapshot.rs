#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SnapshotEnvelope {
    pub view_key: String,
    pub producer_id: String,
    pub event_stream_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub published_at_unix_nanos: u64,
    pub payload: Vec<u8>,
}

use crate::{ContractError, ContractResult};
use std::path::Path;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketSnapshotRead {
    pub generation: u64,
    pub event_sequence: u64,
    pub quotes: Vec<crate::model::Quote>,
}

/// Read the latest quote view from the module-owned mmap snapshot.
///
/// The FlatBuffers decode stays in the Market contract; consumers receive the
/// public `Quote` model and never need to know the generated schema layout.
pub fn read_latest_quotes(path: impl AsRef<Path>) -> ContractResult<Vec<crate::model::Quote>> {
    Ok(read_latest_quotes_with_watermark(path)?.quotes)
}

/// Read only the publication watermark without decoding the quote payload.
/// Consumers can use this to avoid repeatedly rebuilding an unchanged
/// projection from a high-frequency snapshot.
pub fn read_latest_quotes_watermark(path: impl AsRef<Path>) -> ContractResult<(u64, u64)> {
    use kairos_protocol::generated::kairos::market::v_1::{
        market_data_snapshot_buffer_has_identifier, root_as_market_data_snapshot,
    };
    let reader = kairos_transport::SharedSnapshotReader::open(path)
        .map_err(|error| ContractError::Transport(error.to_string()))?;
    let payload = reader
        .read_payload()
        .map_err(ContractError::Transport)?
        .payload;
    if !market_data_snapshot_buffer_has_identifier(&payload) {
        return Err(ContractError::Invalid(
            "Market snapshot has an invalid file identifier".into(),
        ));
    }
    let root = root_as_market_data_snapshot(&payload)
        .map_err(|error| ContractError::Invalid(format!("decode Market snapshot: {error}")))?;
    let header = root.header();
    Ok((header.generation(), header.event_sequence()))
}

pub fn read_latest_quotes_with_watermark(
    path: impl AsRef<Path>,
) -> ContractResult<MarketSnapshotRead> {
    use kairos_protocol::generated::kairos::market::v_1::{
        market_data_snapshot_buffer_has_identifier, root_as_market_data_snapshot,
    };
    let reader = kairos_transport::SharedSnapshotReader::open(path)
        .map_err(|error| ContractError::Transport(error.to_string()))?;
    let payload = reader
        .read_payload()
        .map_err(ContractError::Transport)?
        .payload;
    if !market_data_snapshot_buffer_has_identifier(&payload) {
        return Err(ContractError::Invalid(
            "Market snapshot has an invalid file identifier".into(),
        ));
    }
    let root = root_as_market_data_snapshot(&payload)
        .map_err(|error| ContractError::Invalid(format!("decode Market snapshot: {error}")))?;
    let header = root.header();
    let data = root.payload();
    let quotes = data
        .quotes()
        .map(|quotes| {
            quotes
                .iter()
                .map(|quote| crate::model::Quote {
                    market_id: quote.market_id().unwrap_or_default().to_owned(),
                    instrument_id: quote.instrument_id().to_owned(),
                    bid_price: quote.bid_price().map(decimal_string),
                    bid_quantity: quote.bid_quantity().map(decimal_string),
                    ask_price: quote.ask_price().map(decimal_string),
                    ask_quantity: quote.ask_quantity().map(decimal_string),
                    observed_at_unix_nanos: quote.event_time_unix_nanos(),
                    source_id: quote.source_id().unwrap_or_default().to_owned(),
                })
                .collect()
        })
        .unwrap_or_default();
    Ok(MarketSnapshotRead {
        generation: header.generation(),
        event_sequence: header.event_sequence(),
        quotes,
    })
}

fn decimal_string(value: &kairos_protocol::generated::kairos::common::v_1::Decimal64) -> String {
    let mantissa = value.mantissa();
    let scale = value.scale() as usize;
    let sign = if mantissa < 0 { "-" } else { "" };
    let digits = mantissa.unsigned_abs().to_string();
    if scale == 0 {
        return format!("{sign}{digits}");
    }
    let padded = format!("{digits:0>width$}", width = scale + 1);
    format!(
        "{sign}{}.{}",
        &padded[..padded.len() - scale],
        &padded[padded.len() - scale..]
    )
}
