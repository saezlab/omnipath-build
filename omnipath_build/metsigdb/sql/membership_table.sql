-- MetSigDB unified membership substrate (cycle 010).
--
-- One row is one canonical metabolite entity in one resource-native set.
-- `omnipath-build` writes this table. `omnipath-metabo` reads it and nothing
-- else for /sets/metsigdb.
--
-- The name is unprefixed, following the derived-table convention of this
-- package (canonical_entity, gene_protein_representative). The metabo_* prefix
-- marks tables written by the omnipath-metabo post-build step, and this table
-- is written by the core build.
--
-- The DDL is idempotent: applying it twice changes nothing.

CREATE TABLE IF NOT EXISTS metsigdb_membership (
  -- Row identity. A rebuild refreshes in place, and never duplicates.
  resource              text  NOT NULL,
  set_source_id         text  NOT NULL,
  metabolite_entity_id  uuid  NOT NULL,

  -- Metabolite side, projected from the canonical entity layer.
  metabolite_label       text NOT NULL,
  metabolite_entity_type text NOT NULL,
  inchikey               text,
  smiles                 text,
  hmdb                   text,
  pubchem                text,
  chebi                  text,
  kegg                   text,

  -- Set side. `set_label` is null throughout v1: ontology_terms is empty, so
  -- no set carries a readable name. `organism` is 9606 for Reactome and null
  -- elsewhere, because no set entity carries a taxonomy_id.
  set_label   text,
  set_type    text   NOT NULL,
  organism    bigint,
  set_size    integer NOT NULL,
  set_context jsonb,

  -- Provenance. `provenance_source` cites the pypath.inputs_v2 module and the
  -- commit that parsed the resource, read from the `resources` table.
  provenance_source text NOT NULL,
  provenance_record jsonb,
  build_id          text NOT NULL,

  CONSTRAINT metsigdb_membership_pkey
    PRIMARY KEY (resource, set_source_id, metabolite_entity_id),
  CONSTRAINT metsigdb_membership_set_type_check
    CHECK (set_type IN ('disease', 'pathway', 'chemical_class')),
  CONSTRAINT metsigdb_membership_resource_check
    CHECK (resource IN ('KEGG', 'Reactome', 'WikiPathways', 'MACdb', 'ClassyFire')),
  CONSTRAINT metsigdb_membership_set_size_check
    CHECK (set_size > 0)
);

-- The five shared filter columns of the API contract, and nothing else. The
-- serving layer filters on these alone, so these alone are indexed.
--
-- `resource` and `set_source_id` lead the primary key, so a filter on either
-- already has an index. The three below cover the rest.
CREATE INDEX IF NOT EXISTS metsigdb_membership_metabolite_idx
  ON metsigdb_membership (metabolite_entity_id);

CREATE INDEX IF NOT EXISTS metsigdb_membership_set_type_idx
  ON metsigdb_membership (set_type);

-- Partial: only Reactome rows carry an organism in v1, so the index stays
-- small and an `organism` filter never scans the null-organism bulk.
CREATE INDEX IF NOT EXISTS metsigdb_membership_organism_idx
  ON metsigdb_membership (organism) WHERE organism IS NOT NULL;

-- Paging needs no index of its own. The API contract orders by the row
-- identity, and the primary key already indexes exactly those three columns in
-- that order.

COMMENT ON TABLE metsigdb_membership IS
  'MetSigDB v1: canonical metabolite memberships in resource-native sets from '
  'KEGG, Reactome, WikiPathways, MACdb and ClassyFire (cycle 010).';
