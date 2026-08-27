//! Shared safety checks for FlatBuffers process-boundary frames.

/// Returns whether a non-size-prefixed FlatBuffer is long enough for its root
/// offset and four-byte file identifier to be inspected safely.
///
/// `flatbuffers::buffer_has_identifier` asserts this precondition instead of
/// returning an error, so every owner decoder must check it before dispatching
/// on a generated `*_buffer_has_identifier` accessor.
#[must_use]
pub const fn identifier_is_readable(bytes: &[u8]) -> bool {
    bytes.len() >= 8
}

#[cfg(test)]
mod tests {
    use super::identifier_is_readable;

    #[test]
    fn requires_root_offset_and_identifier() {
        assert!(!identifier_is_readable(&[0; 7]));
        assert!(identifier_is_readable(&[0; 8]));
    }
}
