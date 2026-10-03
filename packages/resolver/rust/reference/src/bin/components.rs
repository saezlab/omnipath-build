//! Exact DSU over ONLY anchorless record vertices. Anchors never enter this graph.
use arrow_array::{Array, RecordBatch, StringArray, UInt64Array};
use arrow_schema::{DataType, Field, Schema};
use parquet::arrow::{arrow_reader::ParquetRecordBatchReaderBuilder, ArrowWriter};
use parquet::basic::{Compression, ZstdLevel};
use parquet::file::properties::WriterProperties;
use std::{fs::File, path::Path, sync::Arc, time::Instant};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
struct Dsu {
    parent: Vec<u32>,
    rank: Vec<u8>,
    minimum: Vec<u32>,
}
impl Dsu {
    fn new(n: usize) -> Result<Self> {
        if n > u32::MAX as usize {
            return Err(
                "anchorless graph exceeds u32 capacity; external reconciliation required".into(),
            );
        }
        Ok(Self {
            parent: (0..n as u32).collect(),
            rank: vec![0; n],
            minimum: (0..n as u32).collect(),
        })
    }
    fn root(&mut self, mut x: u32) -> u32 {
        while self.parent[x as usize] != x {
            self.parent[x as usize] = self.parent[self.parent[x as usize] as usize];
            x = self.parent[x as usize];
        }
        x
    }
    fn union(&mut self, a: u32, b: u32) -> bool {
        let (mut a, mut b) = (self.root(a), self.root(b));
        if a == b {
            return false;
        }
        if self.rank[a as usize] < self.rank[b as usize] {
            std::mem::swap(&mut a, &mut b);
        }
        self.parent[b as usize] = a;
        self.minimum[a as usize] = self.minimum[a as usize].min(self.minimum[b as usize]);
        if self.rank[a as usize] == self.rank[b as usize] {
            self.rank[a as usize] += 1;
        }
        true
    }
    fn component(&mut self, x: u32) -> u32 {
        let root = self.root(x);
        self.minimum[root as usize]
    }
}
fn props() -> WriterProperties {
    WriterProperties::builder()
        .set_compression(Compression::ZSTD(ZstdLevel::default()))
        .set_max_row_group_row_count(Some(131072))
        .build()
}
fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
    if args.len() != 4 {
        return Err("usage: anchor-components VERTEX_COUNT EDGES.parquet OUTPUT_DIR".into());
    }
    let n: usize = args[1].parse()?;
    let out = Path::new(&args[3]);
    std::fs::create_dir_all(out)?;
    let mut dsu = Dsu::new(n)?;
    let start = Instant::now();
    let mut tick = Instant::now();
    let mut processed = 0u64;
    let mut joined = 0u64;
    let witness_schema = Arc::new(Schema::new(vec![
        Field::new("a", DataType::UInt64, false),
        Field::new("b", DataType::UInt64, false),
        Field::new("assertion_id", DataType::Utf8, false),
    ]));
    let mut witnesses = ArrowWriter::try_new(
        File::create(out.join("witnesses.parquet"))?,
        witness_schema.clone(),
        Some(props()),
    )?;
    let reader = ParquetRecordBatchReaderBuilder::try_new(File::open(&args[2])?)?
        .with_batch_size(65536)
        .build()?;
    for batch in reader {
        let batch = batch?;
        let a = batch
            .column_by_name("a")
            .and_then(|v| v.as_any().downcast_ref::<UInt64Array>())
            .ok_or("expected uint64 a")?;
        let b = batch
            .column_by_name("b")
            .and_then(|v| v.as_any().downcast_ref::<UInt64Array>())
            .ok_or("expected uint64 b")?;
        let ids = batch
            .column_by_name("assertion_id")
            .and_then(|v| v.as_any().downcast_ref::<StringArray>())
            .ok_or("expected utf8 assertion_id")?;
        if a.null_count() + b.null_count() + ids.null_count() > 0 {
            return Err("null graph edge".into());
        }
        let (mut wa, mut wb, mut wi) = (Vec::new(), Vec::new(), Vec::new());
        for i in 0..batch.num_rows() {
            let (a, b) = (a.value(i), b.value(i));
            if a >= n as u64 || b >= n as u64 {
                return Err("vertex outside graph".into());
            }
            if dsu.union(a as u32, b as u32) {
                wa.push(a);
                wb.push(b);
                wi.push(ids.value(i).to_owned());
                joined += 1;
            }
            processed += 1;
        }
        if !wa.is_empty() {
            witnesses.write(&RecordBatch::try_new(
                witness_schema.clone(),
                vec![
                    Arc::new(UInt64Array::from(wa)),
                    Arc::new(UInt64Array::from(wb)),
                    Arc::new(StringArray::from(wi)),
                ],
            )?)?;
        }
        if tick.elapsed().as_secs() >= 10 {
            println!(
                "components edges={} unions={} elapsed_s={}",
                processed,
                joined,
                start.elapsed().as_secs()
            );
            tick = Instant::now();
        }
    }
    witnesses.close()?;
    let schema = Arc::new(Schema::new(vec![
        Field::new("vertex", DataType::UInt64, false),
        Field::new("component", DataType::UInt64, false),
    ]));
    let mut writer = ArrowWriter::try_new(
        File::create(out.join("components.parquet"))?,
        schema.clone(),
        Some(props()),
    )?;
    for offset in (0..n).step_by(65536) {
        let end = n.min(offset + 65536);
        let ids: Vec<u64> = (offset as u64..end as u64).collect();
        let components: Vec<u64> = ids
            .iter()
            .map(|i| dsu.component(*i as u32) as u64)
            .collect();
        writer.write(&RecordBatch::try_new(
            schema.clone(),
            vec![
                Arc::new(UInt64Array::from(ids)),
                Arc::new(UInt64Array::from(components)),
            ],
        )?)?;
        if tick.elapsed().as_secs() >= 10 {
            println!(
                "components written_vertices={} total={} elapsed_s={}",
                end,
                n,
                start.elapsed().as_secs()
            );
            tick = Instant::now();
        }
    }
    writer.close()?;
    println!(
        "components complete vertices={} edges={} components={} elapsed_s={:.3}",
        n,
        processed,
        n as u64 - joined,
        start.elapsed().as_secs_f64()
    );
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn order_independent_components() {
        for pairs in [vec![(0, 1), (2, 3), (1, 2)], vec![(2, 3), (1, 2), (0, 1)]] {
            let mut d = Dsu::new(5).unwrap();
            for (a, b) in pairs {
                d.union(a, b);
            }
            assert_eq!(
                (0..5).map(|i| d.component(i)).collect::<Vec<_>>(),
                vec![0, 0, 0, 0, 4]
            );
        }
    }
}
