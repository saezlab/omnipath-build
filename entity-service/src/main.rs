use std::{env, net::SocketAddr, path::Path, sync::Arc};

use anyhow::{Context, Result};
use arrow::array::{Array, Int64Array, LargeStringArray};
use axum::{
    Json, Router,
    extract::State,
    routing::{get, post},
};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use parquet::file::reader::{FileReader, SerializedFileReader};
use rustc_hash::FxHashMap;
use serde::{Deserialize, Serialize};
use tokio::net::TcpListener;
use tracing::{error, info};

#[derive(Clone)]
struct AppState {
    index: Arc<FxHashMap<Arc<str>, Vec<i64>>>,
}

#[derive(Debug, Deserialize)]
struct LookupRequest {
    identifiers: Vec<String>,
}

#[derive(Debug, Serialize)]
struct LookupResponse {
    results: FxHashMap<String, Vec<i64>>,
}

#[tokio::main]
async fn main() -> Result<()> {
    init_tracing();

    let data_path = env::var("ENTITY_PARQUET_PATH")
        .unwrap_or_else(|_| "databases/omnipath/output/entity_identifier.parquet".to_string());
    let bind_addr: SocketAddr = env::var("BIND_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:8080".to_string())
        .parse()
        .context("failed to parse BIND_ADDR")?;

    info!("loading identifiers from {data_path}");
    let index = Arc::new(load_index(&data_path)?);
    info!("loaded {} unique identifiers", index.len());

    let state = AppState { index };
    let app = Router::new()
        .route("/health", get(|| async { "ok" }))
        .route("/lookup", post(lookup))
        .with_state(state);

    let listener = TcpListener::bind(bind_addr)
        .await
        .context("failed to bind TCP listener")?;
    info!("listening on http://{bind_addr}");

    axum::serve(listener, app).await.context("server error")
}

async fn lookup(
    State(state): State<AppState>,
    Json(payload): Json<LookupRequest>,
) -> Json<LookupResponse> {
    let mut results: FxHashMap<String, Vec<i64>> = Default::default();
    results.reserve(payload.identifiers.len());

    for ident in payload.identifiers {
        let matches = state.index.get(ident.as_str()).cloned().unwrap_or_default();
        results.insert(ident, matches);
    }

    Json(LookupResponse { results })
}

fn load_index<P: AsRef<Path>>(parquet_path: P) -> Result<FxHashMap<Arc<str>, Vec<i64>>> {
    let parquet_path = parquet_path.as_ref();

    let metadata_reader = SerializedFileReader::new(
        std::fs::File::open(parquet_path)
            .with_context(|| format!("failed to open {:?}", parquet_path))?,
    )?;
    let estimated_rows = metadata_reader.metadata().file_metadata().num_rows() as usize;

    let file = std::fs::File::open(parquet_path)
        .with_context(|| format!("failed to open {:?}", parquet_path))?;
    let builder = ParquetRecordBatchReaderBuilder::try_new(file)?;
    let schema = builder.schema();

    let entity_idx = schema
        .index_of("entity_id")
        .context("missing entity_id column")?;
    let identifier_idx = schema
        .index_of("identifier")
        .context("missing identifier column")?;

    let mut reader = builder.with_batch_size(8192).build()?;
    let mut index: FxHashMap<Arc<str>, Vec<i64>> = Default::default();
    index.reserve(estimated_rows);

    while let Some(batch) = reader.next() {
        let batch = batch?;
        let entity_ids = batch
            .column(entity_idx)
            .as_any()
            .downcast_ref::<Int64Array>()
            .context("entity_id column must be int64")?;
        let identifiers = batch
            .column(identifier_idx)
            .as_any()
            .downcast_ref::<LargeStringArray>()
            .context("identifier column must be LargeString")?;

        for row in 0..batch.num_rows() {
            if identifiers.is_null(row) {
                continue;
            }
            let entity_id = entity_ids.value(row);
            let ident: Arc<str> = identifiers.value(row).into();
            index.entry(ident).or_default().push(entity_id);
        }
    }

    Ok(index)
}

fn init_tracing() {
    if tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .try_init()
        .is_err()
    {
        error!("failed to init tracing subscriber");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::{
        array::{Int64Array, LargeStringArray},
        datatypes::{DataType, Field, Schema},
        record_batch::RecordBatch,
    };
    use parquet::arrow::arrow_writer::ArrowWriter;
    use std::sync::Arc;
    use tempfile::NamedTempFile;

    #[test]
    fn builds_index_from_parquet() -> Result<()> {
        let schema = Schema::new(vec![
            Field::new("entity_id", DataType::Int64, false),
            Field::new("identifier", DataType::LargeUtf8, false),
        ]);

        let batch = RecordBatch::try_new(
            Arc::new(schema.clone()),
            vec![
                Arc::new(Int64Array::from(vec![1, 2, 2])),
                Arc::new(LargeStringArray::from(vec!["a", "b", "b"])),
            ],
        )?;

        let temp = NamedTempFile::new()?;
        {
            let mut writer = ArrowWriter::try_new(temp.reopen()?, Arc::new(schema), None)?;
            writer.write(&batch)?;
            writer.close()?;
        }

        let index = load_index(temp.path())?;
        assert_eq!(index.get("a"), Some(&vec![1]));
        assert_eq!(index.get("b"), Some(&vec![2, 2]));
        assert!(index.get("c").is_none());
        Ok(())
    }
}
