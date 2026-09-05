//! Typed Reference lifecycle publication preparation.

mod encoding;

pub(crate) use encoding::{
    EncodedPublication, encode_coverage_publications, encode_publication, encode_publications,
};
