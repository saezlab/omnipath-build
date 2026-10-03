//! Adapter for values fetched from the compact disk index.
//! Uses the shared biological decision kernel and does not read Parquet.
use anchor_resolution_serving::*;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::BTreeMap;

// Python supplies only the values retrieved for this bounded batch.
type VoteRow = (Vec<u8>, String, u8, usize, bool, bool);
type QueryRow = (String, u8, Vec<VoteRow>);
type MetaRow = (u64, String, u8, Option<String>, bool, bool);
type OutputRow = (String, String, Vec<String>, usize);

fn kind(value: u8) -> Result<Kind> {
    match value {
        1 => Ok(Kind::Chemical),
        2 => Ok(Kind::Protein),
        3 => Ok(Kind::Gene),
        _ => Err("Invalid entity kind".into()),
    }
}

#[pyfunction]
pub fn resolve_precomputed_batch(
    py: Python<'_>,
    queries: Vec<QueryRow>,
    postings: Vec<(Vec<u8>, Vec<u64>)>,
    metadata: Vec<MetaRow>,
) -> PyResult<Vec<OutputRow>> {
    py.detach(move || -> Result<Vec<OutputRow>> {
        let mut index = MemoryPostings::default();
        for (key, ids) in postings {
            if ids.windows(2).any(|w| w[0] >= w[1]) {
                return Err("Posting IDs must be strictly increasing".into());
            }
            index.0.insert(
                key,
                if ids.is_empty() {
                    Lookup::Missing
                } else {
                    Lookup::Complete(ids)
                },
            );
        }
        let mut meta = MemoryMetadata::default();
        let mut names = BTreeMap::new();
        for (id, name, k, anchor, quarantined, reviewed) in metadata {
            let k = kind(k)?;
            let anchor = match anchor {
                None => None,
                Some(a) if k == Kind::Chemical => Some(Anchor::inchikey(
                    a.strip_prefix("inchikey:").ok_or("Bad chemical anchor")?,
                )?),
                Some(a) if k == Kind::Protein => Some(Anchor::UniProt(
                    a.strip_prefix("uniprot:")
                        .ok_or("Bad protein anchor")?
                        .to_owned(),
                )),
                Some(_) => return Err("Unsupported anchored kind".into()),
            };
            names.insert(id, name);
            meta.0.insert(
                id,
                EntityMeta {
                    id,
                    kind: k,
                    anchor,
                    quarantined,
                    reviewed,
                },
            );
        }
        let mut batches = Vec::new();
        for (_, target, votes) in &queries {
            let mut evidence = Vec::new();
            for (key, anchor, route, ordinal, gene, products) in votes {
                let role = if !anchor.is_empty() {
                    Role::PrimaryAnchor(Anchor::inchikey(anchor)?)
                } else if *route == 2 {
                    Role::GeneToProtein
                } else if *gene {
                    Role::GeneIdentity {
                        multiple_products: *products,
                    }
                } else {
                    Role::Identity
                };
                evidence.push(Evidence {
                    key: key.clone(),
                    role,
                    input_ordinal: *ordinal,
                });
            }
            batches.push(Query {
                target: kind(*target)?,
                evidence,
            });
        }
        let results = resolve_batch(&index, &meta, &batches, &Policy::omnipath())?;
        queries
            .iter()
            .zip(results)
            .map(|((id, _, _), r)| {
                let accepted = r
                    .accepted_ids()
                    .iter()
                    .map(|n| {
                        names
                            .get(n)
                            .cloned()
                            .ok_or_else(|| "Missing entity metadata".into())
                    })
                    .collect::<Result<Vec<_>>>()?;
                Ok((
                    id.clone(),
                    format!("{:?}", r.outcome),
                    accepted,
                    r.candidates.len(),
                ))
            })
            .collect()
    })
    .map_err(|e| PyValueError::new_err(e.to_string()))
}
