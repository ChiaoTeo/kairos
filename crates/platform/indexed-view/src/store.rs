use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use heed::types::Bytes;
use heed::{Database, Env, EnvFlags, EnvOpenOptions};

use crate::metadata::{
    FORMAT_VERSION, IndexedViewIdentity, KEY_APPLIED_EVENT_SEQUENCE, KEY_COMMITTED_AT_UNIX_NANOS,
    KEY_FORMAT_VERSION, KEY_IDENTITY, KEY_PRODUCER_INCARNATION, KEY_REBUILD_STATE,
    KEY_RESOURCE_EPOCH, KEY_SCHEMA_SET, METADATA_DATABASE, MetadataSnapshot, RebuildState,
    decode_rebuild_state, decode_u32, decode_u64, encode_u32, encode_u64,
};

type RawDatabase = Database<Bytes, Bytes>;

#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("invalid indexed-view identity: {0}")]
    InvalidIdentity(String),
    #[error("invalid indexed-view schema: {0}")]
    InvalidSchema(String),
    #[error("invalid indexed-view path: {0}")]
    InvalidPath(String),
    #[error("indexed-view metadata mismatch: {0}")]
    MetadataMismatch(String),
    #[error("corrupt indexed-view metadata: {0}")]
    CorruptMetadata(String),
    #[error("unknown indexed-view database `{0}`")]
    UnknownDatabase(String),
    #[error("indexed-view storage: {0}")]
    Storage(#[from] heed::Error),
    #[error("indexed-view filesystem: {0}")]
    Io(#[from] std::io::Error),
}

#[derive(Clone, Debug)]
pub struct EnvironmentOptions {
    pub path: PathBuf,
    pub map_size: usize,
    pub max_readers: u32,
}

impl EnvironmentOptions {
    pub fn new(path: impl Into<PathBuf>, map_size: usize) -> Result<Self, StoreError> {
        let path = path.into();
        if !path.is_absolute() {
            return Err(StoreError::InvalidPath(
                "LMDB environment path must be absolute".into(),
            ));
        }
        if map_size < 1024 * 1024 {
            return Err(StoreError::InvalidPath(
                "LMDB map size must be at least one MiB".into(),
            ));
        }
        Ok(Self {
            path,
            map_size,
            max_readers: 126,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Mutation {
    Put {
        database: String,
        key: Vec<u8>,
        value: Vec<u8>,
    },
    Delete {
        database: String,
        key: Vec<u8>,
    },
    DeletePrefix {
        database: String,
        prefix: Vec<u8>,
    },
    Clear {
        database: String,
    },
}

pub struct IndexedViewWriter {
    env: Env,
    metadata: RawDatabase,
    databases: BTreeMap<String, RawDatabase>,
    identity: IndexedViewIdentity,
}

impl IndexedViewWriter {
    pub fn create(
        options: &EnvironmentOptions,
        identity: IndexedViewIdentity,
    ) -> Result<Self, StoreError> {
        fs::create_dir_all(&options.path)?;
        let env = open_env(options, false, identity.schema_set.descriptors().len() + 1)?;
        let mut txn = env.write_txn()?;
        let metadata: RawDatabase = env.create_database(&mut txn, Some(METADATA_DATABASE))?;
        let mut databases = BTreeMap::new();
        for schema in identity.schema_set.descriptors() {
            let database = env.create_database(&mut txn, Some(&schema.database))?;
            databases.insert(schema.database.clone(), database);
        }
        initialize_or_validate_metadata(&metadata, &mut txn, &identity)?;
        metadata.put(
            &mut txn,
            KEY_PRODUCER_INCARNATION,
            &encode_u64(identity.producer_incarnation),
        )?;
        metadata.put(
            &mut txn,
            KEY_REBUILD_STATE,
            &RebuildState::Building.encode()?,
        )?;
        txn.commit()?;
        Ok(Self {
            env,
            metadata,
            databases,
            identity,
        })
    }

    pub fn identity(&self) -> &IndexedViewIdentity {
        &self.identity
    }

    pub fn apply(
        &mut self,
        mutations: &[Mutation],
        applied_event_sequence: u64,
        committed_at_unix_nanos: u64,
    ) -> Result<(), StoreError> {
        let mut txn = self.env.write_txn()?;
        for mutation in mutations {
            match mutation {
                Mutation::Put {
                    database,
                    key,
                    value,
                } => {
                    validate_key(key)?;
                    self.database(database)?.put(&mut txn, key, value)?;
                },
                Mutation::Delete { database, key } => {
                    validate_key(key)?;
                    self.database(database)?.delete(&mut txn, key)?;
                },
                Mutation::DeletePrefix { database, prefix } => {
                    if prefix.is_empty() {
                        return Err(StoreError::InvalidSchema(
                            "prefix deletion requires a non-empty canonical prefix".into(),
                        ));
                    }
                    let database = self.database(database)?;
                    let keys = database
                        .prefix_iter(&txn, prefix)?
                        .map(|row| row.map(|(key, _)| key.to_owned()))
                        .collect::<Result<Vec<_>, _>>()?;
                    for key in keys {
                        database.delete(&mut txn, &key)?;
                    }
                },
                Mutation::Clear { database } => self.database(database)?.clear(&mut txn)?,
            }
        }
        self.metadata.put(
            &mut txn,
            KEY_APPLIED_EVENT_SEQUENCE,
            &encode_u64(applied_event_sequence),
        )?;
        self.metadata.put(
            &mut txn,
            KEY_COMMITTED_AT_UNIX_NANOS,
            &encode_u64(committed_at_unix_nanos),
        )?;
        self.metadata
            .put(&mut txn, KEY_REBUILD_STATE, &RebuildState::Ready.encode()?)?;
        txn.commit()?;
        Ok(())
    }

    pub fn mark_failed(&mut self, diagnostic_code: impl Into<String>) -> Result<(), StoreError> {
        let state = RebuildState::Failed {
            diagnostic_code: diagnostic_code.into(),
        };
        let mut txn = self.env.write_txn()?;
        self.metadata
            .put(&mut txn, KEY_REBUILD_STATE, &state.encode()?)?;
        txn.commit()?;
        Ok(())
    }

    fn database(&self, name: &str) -> Result<RawDatabase, StoreError> {
        self.databases
            .get(name)
            .copied()
            .ok_or_else(|| StoreError::UnknownDatabase(name.to_owned()))
    }
}

pub struct IndexedViewReader {
    env: Env,
    metadata: RawDatabase,
    databases: BTreeMap<String, RawDatabase>,
    identity: IndexedViewIdentity,
}

#[derive(Clone, Copy, Debug)]
pub struct PrefixRequest<'a> {
    pub database: &'a str,
    pub prefix: &'a [u8],
    pub limit: usize,
}

#[derive(Clone, Debug)]
pub struct ReadSnapshot {
    pub metadata: MetadataSnapshot,
    pub rows: BTreeMap<String, Vec<(Vec<u8>, Vec<u8>)>>,
}

#[derive(Clone, Debug)]
pub struct ValueSnapshot {
    pub metadata: MetadataSnapshot,
    pub value: Option<Vec<u8>>,
}

impl IndexedViewReader {
    pub fn open(
        options: &EnvironmentOptions,
        expected: IndexedViewIdentity,
    ) -> Result<Self, StoreError> {
        if !options.path.is_dir() {
            return Err(StoreError::InvalidPath(format!(
                "LMDB environment does not exist: {}",
                options.path.display()
            )));
        }
        let env = open_env(options, true, expected.schema_set.descriptors().len() + 1)?;
        let txn = env.read_txn()?;
        let metadata: RawDatabase = env
            .open_database(&txn, Some(METADATA_DATABASE))?
            .ok_or_else(|| StoreError::CorruptMetadata("metadata database is missing".into()))?;
        validate_existing_metadata(&metadata, &txn, &expected)?;
        let mut databases = BTreeMap::new();
        for schema in expected.schema_set.descriptors() {
            let database = env
                .open_database(&txn, Some(&schema.database))?
                .ok_or_else(|| {
                    StoreError::MetadataMismatch(format!(
                        "declared database `{}` is missing",
                        schema.database
                    ))
                })?;
            databases.insert(schema.database.clone(), database);
        }
        txn.commit()?;
        Ok(Self {
            env,
            metadata,
            databases,
            identity: expected,
        })
    }

    pub fn identity(&self) -> &IndexedViewIdentity {
        &self.identity
    }

    pub fn metadata(&self) -> Result<MetadataSnapshot, StoreError> {
        let txn = self.env.read_txn()?;
        let snapshot = read_metadata(&self.metadata, &txn)?;
        txn.commit()?;
        Ok(snapshot)
    }

    pub fn get(&self, database: &str, key: &[u8]) -> Result<Option<Vec<u8>>, StoreError> {
        self.with_value(database, key, |value| value.map(ToOwned::to_owned))
    }

    /// Reads owner metadata and one exact value from the same LMDB read
    /// transaction. The returned bytes are owned so no LMDB address escapes
    /// the transaction lifetime.
    pub fn value_snapshot(&self, database: &str, key: &[u8]) -> Result<ValueSnapshot, StoreError> {
        self.with_value_snapshot(database, key, |metadata, value| ValueSnapshot {
            metadata,
            value: value.map(ToOwned::to_owned),
        })
    }

    /// Reads metadata and one value in the same short read transaction while
    /// keeping the value borrowed from LMDB for the duration of `read`.
    pub fn with_value_snapshot<R>(
        &self,
        database: &str,
        key: &[u8],
        read: impl FnOnce(MetadataSnapshot, Option<&[u8]>) -> R,
    ) -> Result<R, StoreError> {
        validate_key(key)?;
        let txn = self.env.read_txn()?;
        let metadata = read_metadata(&self.metadata, &txn)?;
        let value = self.database(database)?.get(&txn, key)?;
        let result = read(metadata, value);
        txn.commit()?;
        Ok(result)
    }

    /// Reads a value directly from LMDB while its short read transaction is
    /// alive. The callback's borrowed slice cannot escape this method.
    pub fn with_value<R>(
        &self,
        database: &str,
        key: &[u8],
        read: impl FnOnce(Option<&[u8]>) -> R,
    ) -> Result<R, StoreError> {
        validate_key(key)?;
        let txn = self.env.read_txn()?;
        let value = self.database(database)?.get(&txn, key)?;
        let result = read(value);
        txn.commit()?;
        Ok(result)
    }

    pub fn prefix(
        &self,
        database: &str,
        prefix: &[u8],
        limit: usize,
    ) -> Result<Vec<(Vec<u8>, Vec<u8>)>, StoreError> {
        self.map_prefix(database, prefix, limit, |key, value| {
            (key.to_owned(), value.to_owned())
        })
    }

    /// Maps a bounded prefix range while key and value slices remain borrowed
    /// from one short LMDB read transaction.
    pub fn map_prefix<R>(
        &self,
        database: &str,
        prefix: &[u8],
        limit: usize,
        mut map: impl FnMut(&[u8], &[u8]) -> R,
    ) -> Result<Vec<R>, StoreError> {
        if prefix.is_empty() {
            return Err(StoreError::InvalidSchema(
                "prefix reads require a non-empty canonical prefix".into(),
            ));
        }
        if limit == 0 {
            return Err(StoreError::InvalidSchema(
                "prefix reads require a positive limit".into(),
            ));
        }
        let txn = self.env.read_txn()?;
        let mut rows = Vec::new();
        for result in self.database(database)?.prefix_iter(&txn, prefix)? {
            let (key, value) = result?;
            rows.push(map(key, value));
            if rows.len() == limit {
                break;
            }
        }
        txn.commit()?;
        Ok(rows)
    }

    /// Reads owner metadata and maps a bounded prefix range from the same
    /// short LMDB read transaction. Keys and values cannot escape the
    /// callback unless the caller deliberately returns owned data.
    pub fn map_prefix_snapshot<R>(
        &self,
        database: &str,
        prefix: &[u8],
        limit: usize,
        mut map: impl FnMut(&MetadataSnapshot, &[u8], &[u8]) -> R,
    ) -> Result<(MetadataSnapshot, Vec<R>), StoreError> {
        if prefix.is_empty() {
            return Err(StoreError::InvalidSchema(
                "prefix reads require a non-empty canonical prefix".into(),
            ));
        }
        if limit == 0 {
            return Err(StoreError::InvalidSchema(
                "prefix reads require a positive limit".into(),
            ));
        }
        let txn = self.env.read_txn()?;
        let metadata = read_metadata(&self.metadata, &txn)?;
        let mut rows = Vec::new();
        for result in self.database(database)?.prefix_iter(&txn, prefix)? {
            let (key, value) = result?;
            rows.push(map(&metadata, key, value));
            if rows.len() == limit {
                break;
            }
        }
        txn.commit()?;
        Ok((metadata, rows))
    }

    /// Reads metadata and multiple named-database ranges from one LMDB read
    /// transaction, preserving an atomic owner snapshot while returning owned bytes.
    pub fn snapshot(&self, requests: &[PrefixRequest<'_>]) -> Result<ReadSnapshot, StoreError> {
        let txn = self.env.read_txn()?;
        let metadata = read_metadata(&self.metadata, &txn)?;
        let mut all_rows = BTreeMap::new();
        for request in requests {
            if request.prefix.is_empty() || request.limit == 0 {
                return Err(StoreError::InvalidSchema(
                    "snapshot prefix reads require a non-empty prefix and positive limit".into(),
                ));
            }
            let mut rows = Vec::new();
            for result in self
                .database(request.database)?
                .prefix_iter(&txn, request.prefix)?
            {
                let (key, value) = result?;
                rows.push((key.to_owned(), value.to_owned()));
                if rows.len() == request.limit {
                    break;
                }
            }
            if all_rows.insert(request.database.to_owned(), rows).is_some() {
                return Err(StoreError::InvalidSchema(format!(
                    "snapshot request repeats database `{}`",
                    request.database
                )));
            }
        }
        txn.commit()?;
        Ok(ReadSnapshot {
            metadata,
            rows: all_rows,
        })
    }

    /// Maps several named-database ranges inside one short LMDB read
    /// transaction. The callback sees borrowed key/value slices, so callers
    /// can project directly into their final owned representation without an
    /// intermediate raw-byte snapshot.
    pub fn try_map_snapshot<R, E>(
        &self,
        requests: &[PrefixRequest<'_>],
        mut map: impl FnMut(&MetadataSnapshot, &str, &[u8], &[u8]) -> Result<R, E>,
    ) -> Result<Result<(MetadataSnapshot, BTreeMap<String, Vec<R>>), E>, StoreError> {
        let txn = self.env.read_txn()?;
        let metadata = read_metadata(&self.metadata, &txn)?;
        let mut all_rows = BTreeMap::new();
        let mut mapping_error = None;
        for request in requests {
            if request.prefix.is_empty() || request.limit == 0 {
                return Err(StoreError::InvalidSchema(
                    "snapshot prefix reads require a non-empty prefix and positive limit".into(),
                ));
            }
            if all_rows.contains_key(request.database) {
                return Err(StoreError::InvalidSchema(format!(
                    "snapshot request repeats database `{}`",
                    request.database
                )));
            }
            let mut rows = Vec::new();
            for result in self
                .database(request.database)?
                .prefix_iter(&txn, request.prefix)?
            {
                let (key, value) = result?;
                match map(&metadata, request.database, key, value) {
                    Ok(value) => rows.push(value),
                    Err(error) => {
                        mapping_error = Some(error);
                        break;
                    },
                }
                if rows.len() == request.limit {
                    break;
                }
            }
            all_rows.insert(request.database.to_owned(), rows);
            if mapping_error.is_some() {
                break;
            }
        }
        txn.commit()?;
        if let Some(error) = mapping_error {
            Ok(Err(error))
        } else {
            Ok(Ok((metadata, all_rows)))
        }
    }

    fn database(&self, name: &str) -> Result<RawDatabase, StoreError> {
        self.databases
            .get(name)
            .copied()
            .ok_or_else(|| StoreError::UnknownDatabase(name.to_owned()))
    }
}

fn open_env(
    options: &EnvironmentOptions,
    read_only: bool,
    database_count: usize,
) -> Result<Env, StoreError> {
    let mut builder = EnvOpenOptions::new();
    builder
        .map_size(options.map_size)
        .max_readers(options.max_readers)
        .max_dbs(database_count as u32);
    if read_only {
        unsafe {
            builder.flags(EnvFlags::READ_ONLY);
        }
    }
    // SAFETY: Kairos owns this epoch-specific directory, never mixes LMDB
    // implementations for it, and validates its metadata before exposing it.
    unsafe { builder.open(&options.path) }.map_err(StoreError::from)
}

fn initialize_or_validate_metadata(
    metadata: &RawDatabase,
    txn: &mut heed::RwTxn<'_>,
    identity: &IndexedViewIdentity,
) -> Result<(), StoreError> {
    if metadata.get(txn, KEY_FORMAT_VERSION)?.is_some() {
        return validate_existing_metadata(metadata, txn, identity);
    }
    metadata.put(txn, KEY_IDENTITY, &identity.encode_identity()?)?;
    metadata.put(txn, KEY_FORMAT_VERSION, &encode_u32(FORMAT_VERSION))?;
    metadata.put(txn, KEY_SCHEMA_SET, &identity.schema_set.encode()?)?;
    metadata.put(
        txn,
        KEY_RESOURCE_EPOCH,
        &encode_u64(identity.resource_epoch),
    )?;
    metadata.put(
        txn,
        KEY_PRODUCER_INCARNATION,
        &encode_u64(identity.producer_incarnation),
    )?;
    metadata.put(txn, KEY_APPLIED_EVENT_SEQUENCE, &encode_u64(0))?;
    metadata.put(txn, KEY_COMMITTED_AT_UNIX_NANOS, &encode_u64(0))?;
    metadata.put(txn, KEY_REBUILD_STATE, &RebuildState::Building.encode()?)?;
    Ok(())
}

fn validate_existing_metadata(
    metadata: &RawDatabase,
    txn: &heed::RoTxn<'_>,
    expected: &IndexedViewIdentity,
) -> Result<(), StoreError> {
    compare_metadata(metadata, txn, KEY_IDENTITY, &expected.encode_identity()?)?;
    compare_metadata(
        metadata,
        txn,
        KEY_FORMAT_VERSION,
        &encode_u32(FORMAT_VERSION),
    )?;
    compare_metadata(
        metadata,
        txn,
        KEY_SCHEMA_SET,
        &expected.schema_set.encode()?,
    )?;
    compare_metadata(
        metadata,
        txn,
        KEY_RESOURCE_EPOCH,
        &encode_u64(expected.resource_epoch),
    )?;
    Ok(())
}

fn compare_metadata(
    metadata: &RawDatabase,
    txn: &heed::RoTxn<'_>,
    key: &[u8],
    expected: &[u8],
) -> Result<(), StoreError> {
    let actual = required_metadata(metadata, txn, key)?;
    if actual != expected {
        return Err(StoreError::MetadataMismatch(
            String::from_utf8_lossy(key).into_owned(),
        ));
    }
    Ok(())
}

fn read_metadata(
    metadata: &RawDatabase,
    txn: &heed::RoTxn<'_>,
) -> Result<MetadataSnapshot, StoreError> {
    Ok(MetadataSnapshot {
        format_version: decode_u32(
            required_metadata(metadata, txn, KEY_FORMAT_VERSION)?,
            "format_version",
        )?,
        resource_epoch: decode_u64(
            required_metadata(metadata, txn, KEY_RESOURCE_EPOCH)?,
            "resource_epoch",
        )?,
        producer_incarnation: decode_u64(
            required_metadata(metadata, txn, KEY_PRODUCER_INCARNATION)?,
            "producer_incarnation",
        )?,
        applied_event_sequence: decode_u64(
            required_metadata(metadata, txn, KEY_APPLIED_EVENT_SEQUENCE)?,
            "applied_event_sequence",
        )?,
        committed_at_unix_nanos: decode_u64(
            required_metadata(metadata, txn, KEY_COMMITTED_AT_UNIX_NANOS)?,
            "committed_at_unix_nanos",
        )?,
        rebuild_state: decode_rebuild_state(required_metadata(metadata, txn, KEY_REBUILD_STATE)?)?,
    })
}

fn required_metadata<'txn>(
    metadata: &RawDatabase,
    txn: &'txn heed::RoTxn<'_>,
    key: &[u8],
) -> Result<&'txn [u8], StoreError> {
    metadata.get(txn, key)?.ok_or_else(|| {
        StoreError::CorruptMetadata(format!(
            "required key `{}` is missing",
            String::from_utf8_lossy(key)
        ))
    })
}

fn validate_key(key: &[u8]) -> Result<(), StoreError> {
    if key.is_empty() {
        return Err(StoreError::InvalidSchema(
            "current-view keys must not be empty".into(),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::*;
    use crate::{SchemaDescriptor, SchemaSet, environment_path};

    fn identity(incarnation: u64) -> IndexedViewIdentity {
        IndexedViewIdentity::new(
            "workspace",
            Some("launch"),
            Some("instance"),
            "Execution",
            "execution-main",
            1,
            incarnation,
            SchemaSet::new([
                SchemaDescriptor::new("orders", 1, "EO03", 1).unwrap(),
                SchemaDescriptor::new("intents", 1, "EI03", 1).unwrap(),
            ])
            .unwrap(),
        )
        .unwrap()
    }

    fn options(root: &Path, identity: &IndexedViewIdentity) -> EnvironmentOptions {
        EnvironmentOptions::new(environment_path(root, identity).unwrap(), 8 * 1024 * 1024).unwrap()
    }

    #[test]
    fn one_commit_is_visible_across_databases() {
        let root = tempfile::tempdir().unwrap();
        let identity = identity(1);
        let options = options(root.path(), &identity);
        let mut writer = IndexedViewWriter::create(&options, identity.clone()).unwrap();
        writer
            .apply(
                &[
                    Mutation::Put {
                        database: "orders".into(),
                        key: b"order-1".to_vec(),
                        value: b"open".to_vec(),
                    },
                    Mutation::Put {
                        database: "intents".into(),
                        key: b"intent-1".to_vec(),
                        value: b"active".to_vec(),
                    },
                ],
                41,
                99,
            )
            .unwrap();
        drop(writer);
        let reader = IndexedViewReader::open(&options, identity).unwrap();
        assert_eq!(
            reader.get("orders", b"order-1").unwrap(),
            Some(b"open".to_vec())
        );
        assert_eq!(
            reader.get("intents", b"intent-1").unwrap(),
            Some(b"active".to_vec())
        );
        assert_eq!(reader.metadata().unwrap().applied_event_sequence, 41);
        assert_eq!(
            reader.metadata().unwrap().rebuild_state,
            RebuildState::Ready
        );
    }

    #[test]
    fn identity_mismatch_is_a_hard_error() {
        let root = tempfile::tempdir().unwrap();
        let first = identity(1);
        let options = options(root.path(), &first);
        let writer = IndexedViewWriter::create(&options, first).unwrap();
        drop(writer);
        let mut mismatched = identity(1);
        mismatched.owner = "Risk".into();
        let error = IndexedViewReader::open(&options, mismatched).err().unwrap();
        assert!(matches!(error, StoreError::MetadataMismatch(_)));
    }

    #[test]
    fn writer_restart_updates_incarnation_without_replacing_epoch() {
        let root = tempfile::tempdir().unwrap();
        let first = identity(1);
        let options = options(root.path(), &first);
        let writer = IndexedViewWriter::create(&options, first).unwrap();
        drop(writer);

        let second = identity(2);
        let writer = IndexedViewWriter::create(&options, second.clone()).unwrap();
        drop(writer);
        let reader = IndexedViewReader::open(&options, second).unwrap();
        assert_eq!(reader.metadata().unwrap().producer_incarnation, 2);
        assert_eq!(
            reader.metadata().unwrap().rebuild_state,
            RebuildState::Building
        );
    }

    #[test]
    fn prefix_reads_are_ordered_and_bounded() {
        let root = tempfile::tempdir().unwrap();
        let identity = identity(1);
        let options = options(root.path(), &identity);
        let mut writer = IndexedViewWriter::create(&options, identity.clone()).unwrap();
        writer
            .apply(
                &[
                    Mutation::Put {
                        database: "orders".into(),
                        key: b"a/2".to_vec(),
                        value: vec![2],
                    },
                    Mutation::Put {
                        database: "orders".into(),
                        key: b"a/1".to_vec(),
                        value: vec![1],
                    },
                    Mutation::Put {
                        database: "orders".into(),
                        key: b"b/1".to_vec(),
                        value: vec![3],
                    },
                ],
                1,
                1,
            )
            .unwrap();
        drop(writer);
        let reader = IndexedViewReader::open(&options, identity).unwrap();
        let rows = reader.prefix("orders", b"a/", 1).unwrap();
        assert_eq!(rows, vec![(b"a/1".to_vec(), vec![1])]);
        assert!(reader.prefix("orders", b"a/", 0).is_err());

        let mapped = reader
            .map_prefix("orders", b"a/", 2, |key, value| (key.len(), value[0]))
            .unwrap();
        assert_eq!(mapped, vec![(3, 1), (3, 2)]);
    }

    #[test]
    fn prefix_replacement_is_atomic_with_the_new_value() {
        let root = tempfile::tempdir().unwrap();
        let identity = identity(1);
        let options = options(root.path(), &identity);
        let mut writer = IndexedViewWriter::create(&options, identity.clone()).unwrap();
        writer
            .apply(
                &[Mutation::Put {
                    database: "orders".into(),
                    key: b"series/1".to_vec(),
                    value: b"old".to_vec(),
                }],
                1,
                1,
            )
            .unwrap();
        writer
            .apply(
                &[
                    Mutation::DeletePrefix {
                        database: "orders".into(),
                        prefix: b"series/".to_vec(),
                    },
                    Mutation::Put {
                        database: "orders".into(),
                        key: b"series/2".to_vec(),
                        value: b"new".to_vec(),
                    },
                ],
                2,
                2,
            )
            .unwrap();
        drop(writer);
        let reader = IndexedViewReader::open(&options, identity).unwrap();
        assert_eq!(
            reader.prefix("orders", b"series/", 10).unwrap(),
            vec![(b"series/2".to_vec(), b"new".to_vec())]
        );
    }

    #[test]
    fn snapshot_reads_metadata_and_families_from_one_transaction() {
        let root = tempfile::tempdir().unwrap();
        let identity = identity(1);
        let options = options(root.path(), &identity);
        let mut writer = IndexedViewWriter::create(&options, identity.clone()).unwrap();
        writer
            .apply(
                &[
                    Mutation::Put {
                        database: "orders".into(),
                        key: b"entity/order-1".to_vec(),
                        value: b"open".to_vec(),
                    },
                    Mutation::Put {
                        database: "intents".into(),
                        key: b"entity/intent-1".to_vec(),
                        value: b"active".to_vec(),
                    },
                ],
                7,
                11,
            )
            .unwrap();
        drop(writer);
        let reader = IndexedViewReader::open(&options, identity).unwrap();
        let snapshot = reader
            .snapshot(&[
                PrefixRequest {
                    database: "orders",
                    prefix: b"entity/",
                    limit: usize::MAX,
                },
                PrefixRequest {
                    database: "intents",
                    prefix: b"entity/",
                    limit: usize::MAX,
                },
            ])
            .unwrap();
        assert_eq!(snapshot.metadata.applied_event_sequence, 7);
        assert_eq!(snapshot.rows["orders"][0].1, b"open");
        assert_eq!(snapshot.rows["intents"][0].1, b"active");
    }

    #[test]
    fn mapped_snapshot_projects_borrowed_rows_without_a_raw_byte_snapshot() {
        let root = tempfile::tempdir().unwrap();
        let identity = identity(1);
        let options = options(root.path(), &identity);
        let mut writer = IndexedViewWriter::create(&options, identity.clone()).unwrap();
        writer
            .apply(
                &[
                    Mutation::Put {
                        database: "orders".into(),
                        key: b"entity/order-1".to_vec(),
                        value: b"open".to_vec(),
                    },
                    Mutation::Put {
                        database: "intents".into(),
                        key: b"entity/intent-1".to_vec(),
                        value: b"active".to_vec(),
                    },
                ],
                19,
                23,
            )
            .unwrap();
        drop(writer);
        let reader = IndexedViewReader::open(&options, identity).unwrap();
        let mapped = reader
            .try_map_snapshot(
                &[
                    PrefixRequest {
                        database: "orders",
                        prefix: b"entity/",
                        limit: usize::MAX,
                    },
                    PrefixRequest {
                        database: "intents",
                        prefix: b"entity/",
                        limit: usize::MAX,
                    },
                ],
                |metadata, database, key, value| {
                    Ok::<_, std::convert::Infallible>(format!(
                        "{}:{database}:{}={}",
                        metadata.applied_event_sequence,
                        std::str::from_utf8(key).unwrap(),
                        std::str::from_utf8(value).unwrap()
                    ))
                },
            )
            .unwrap()
            .unwrap();
        assert_eq!(mapped.0.applied_event_sequence, 19);
        assert_eq!(mapped.1["orders"], ["19:orders:entity/order-1=open"]);
        assert_eq!(mapped.1["intents"], ["19:intents:entity/intent-1=active"]);
    }

    #[test]
    fn value_snapshot_reads_exact_value_and_metadata_atomically() {
        let root = tempfile::tempdir().unwrap();
        let identity = identity(1);
        let options = options(root.path(), &identity);
        let mut writer = IndexedViewWriter::create(&options, identity.clone()).unwrap();
        writer
            .apply(
                &[Mutation::Put {
                    database: "orders".into(),
                    key: b"order-1".to_vec(),
                    value: b"open".to_vec(),
                }],
                17,
                23,
            )
            .unwrap();
        drop(writer);

        let reader = IndexedViewReader::open(&options, identity).unwrap();
        let snapshot = reader.value_snapshot("orders", b"order-1").unwrap();
        assert_eq!(snapshot.metadata.applied_event_sequence, 17);
        assert_eq!(snapshot.metadata.committed_at_unix_nanos, 23);
        assert_eq!(snapshot.value, Some(b"open".to_vec()));

        let inspected = reader
            .with_value_snapshot("orders", b"order-1", |metadata, value| {
                (
                    metadata.applied_event_sequence,
                    value.map(|value| value == b"open"),
                )
            })
            .unwrap();
        assert_eq!(inspected, (17, Some(true)));
    }

    #[test]
    fn scoped_value_callback_reads_the_mmap_slice_before_transaction_end() {
        let root = tempfile::tempdir().unwrap();
        let identity = identity(1);
        let options = options(root.path(), &identity);
        let mut writer = IndexedViewWriter::create(&options, identity.clone()).unwrap();
        writer
            .apply(
                &[Mutation::Put {
                    database: "orders".into(),
                    key: b"order-1".to_vec(),
                    value: b"open".to_vec(),
                }],
                1,
                1,
            )
            .unwrap();
        drop(writer);
        let reader = IndexedViewReader::open(&options, identity).unwrap();
        let decoded = reader
            .with_value("orders", b"order-1", |value| {
                std::str::from_utf8(value.unwrap()).unwrap().to_owned()
            })
            .unwrap();
        assert_eq!(decoded, "open");
    }
}
