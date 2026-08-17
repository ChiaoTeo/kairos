use kairos_conflux::{
    AeronContract, Contract, EnsureDisposition, ManagedClients, ManagedConnections, NoViews,
    ResourceError, RestContract,
};

struct ReferenceRest;

impl RestContract for ReferenceRest {
    type Client = String;
}

struct ReferenceAeron;

impl AeronContract for ReferenceAeron {
    type Frame = u64;
    type Stream = ();
}

struct Reference;

impl Contract for Reference {
    type Endpoint = ();
    type Rest = ReferenceRest;
    type View = NoViews;
    type Aeron = ReferenceAeron;
    type Client = String;
}

#[derive(Debug, PartialEq, Eq)]
struct BinanceSpot(&'static str);

#[test]
fn same_contract_type_has_multiple_named_clients() {
    let mut clients = ManagedClients::<&str, Reference>::new();

    assert_eq!(
        clients.ensure_with("primary", 1, || "reference-a".to_owned()),
        Ok(EnsureDisposition::Created)
    );
    assert_eq!(
        clients.ensure_with("backup", 4, || "reference-b".to_owned()),
        Ok(EnsureDisposition::Created)
    );
    assert_eq!(clients.len(), 2);
    assert_eq!(
        clients.get(&"primary").map(|entry| entry.client().as_str()),
        Some("reference-a")
    );
}

#[test]
fn replacement_advances_epoch_and_rejects_stale_revision() {
    let mut connections = ManagedConnections::<&str, BinanceSpot>::new();
    assert_eq!(
        connections.ensure_with("orders", 7, || BinanceSpot("first")),
        Ok(EnsureDisposition::Created)
    );
    assert_eq!(
        connections.ensure_with("orders", 8, || BinanceSpot("second")),
        Ok(EnsureDisposition::Replaced)
    );

    let current = connections.get(&"orders").expect("connection exists");
    assert_eq!(current.connection(), &BinanceSpot("second"));
    assert_eq!(current.revision(), 8);
    assert_eq!(current.epoch(), 1);
    assert_eq!(
        connections.ensure_with("orders", 6, || BinanceSpot("stale")),
        Err(ResourceError::StaleRevision {
            current: 8,
            received: 6,
        })
    );
}
