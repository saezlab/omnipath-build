Run server: ENTITY_PARQUET_PATH=../databases/omnipath/output/entity_identifier.parquet cargo run --release

Run benchmark: python bench_lookup.py --parquet-path ../databases/omnipath/output/entity_identifier.parquet \
  --sample-size 2000 --batch-size 256 --runs 5 --warmup 1 --url http://localhost:8080/lookup
