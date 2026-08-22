use sqlx::{Row, SqlitePool};

use crate::services::publication::EncodedPublication;

pub(crate) async fn pending_publications(
    pool: &SqlitePool,
    limit: usize,
) -> sqlx::Result<Vec<EncodedPublication>> {
    let rows = sqlx::query(
        "SELECT sequence,event_id,payload FROM reference_publication_outbox ORDER BY sequence LIMIT ?",
    )
    .bind(limit as i64)
    .fetch_all(pool)
    .await?;
    rows.into_iter()
        .map(|row| {
            Ok(EncodedPublication {
                sequence: row.try_get::<i64, _>("sequence")? as u64,
                event_id: row.try_get("event_id")?,
                payload: row.try_get("payload")?,
            })
        })
        .collect::<Result<Vec<_>, sqlx::Error>>()
}

pub(crate) async fn pending_event_count(pool: &SqlitePool) -> sqlx::Result<usize> {
    Ok(
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM reference_publication_outbox")
            .fetch_one(pool)
            .await? as usize,
    )
}

pub(crate) async fn acknowledge_publications(
    pool: &SqlitePool,
    event_ids: &[String],
) -> sqlx::Result<()> {
    let mut sequences = event_ids
        .iter()
        .filter_map(|id| id.rsplit(':').next()?.parse::<i64>().ok())
        .collect::<Vec<_>>();
    sequences.sort_unstable();
    sequences.dedup();

    let mut tx = pool.begin().await?;
    let current = sqlx::query_scalar::<_, i64>(
        "SELECT published_sequence FROM reference_publication_state WHERE id = 1",
    )
    .fetch_one(&mut *tx)
    .await?;
    let mut next = current;
    for sequence in sequences {
        if sequence == next + 1 {
            next = next.max(sequence);
        } else if sequence > next + 1 {
            break;
        }
    }
    if next > current {
        sqlx::query("UPDATE reference_publication_state SET published_sequence = ? WHERE id = 1")
            .bind(next)
            .execute(&mut *tx)
            .await?;
        sqlx::query("DELETE FROM reference_publication_outbox WHERE sequence <= ?")
            .bind(next)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await
}
