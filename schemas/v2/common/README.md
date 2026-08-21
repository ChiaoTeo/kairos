# Common v2 contract boundary

`common/v2` contains only transport-independent contract primitives shared by
more than one owner. It does not contain business state, aggregate snapshots,
provider payloads, or command-specific inputs.

The files are divided by contract shape:

- `metadata.fbs` defines event and current-view publication metadata.
- `decimal.fbs` defines the wire representation for exact decimal values.
- `types.fbs` contains only genuinely cross-owner vocabulary and evidence
  references.

Commands are not FlatBuffers roots. Business commands live in the owning
module's Rust JSON-RPC contract trait. Events and current views use
`EventMetadata` and `ViewMetadata` respectively. Generated types remain at the
process boundary and must not enter application facades or domain models.
