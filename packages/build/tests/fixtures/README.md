# Fixed contract fixtures

Captured on 6 September 2026 before removing the previous implementation.
These JSON files are expected results, not an executable reference backend.

- `resolution_contract.json`: 240 mixed gene/protein and chemical observations,
  including conflicting identifiers and taxa, with expected deferred-alias
  resolution results from the previous matcher against `library_fixture.py`.
- `resource_contract.json`: complete entities, relations and payloads for the
  records defined in `test_regression_contract.py`, including symmetric and
  qualified relations, ontology context and reference aliases.

Tests compare these fixed expectations across shuffled and repeated batches.
Do not regenerate expectations from the implementation under test merely to
make a regression pass. Change them only for a reviewed contract change.

The gene-target expansion contract changes P04637's reference aliases: the
separate A0A0U1RQF1 protein and its entry name are no longer aliases of P04637;
Q15086 remains a secondary accession and is also accepted as a UniProt lookup.
Only these explicit alias changes were applied to resource_contract.json.

The September 9 consolidation applies the already-established structure-first
contract to the chemical cases: a single supplied full InChIKey selects that
structure despite conflicting database aliases; conflicting supplied structures
stay unmatched. The explicit chemical expectations were reviewed against the
fixture identities and the focused chemical-target tests. Observation inputs,
observed aliases, gene/protein results and resource output expectations are unchanged.
