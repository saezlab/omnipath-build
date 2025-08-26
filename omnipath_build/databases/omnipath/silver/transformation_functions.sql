-- =====================================================
-- GENERIC UTILITIES
-- =====================================================

CREATE OR REPLACE MACRO constant_value(field, value) AS 
    value;

CREATE OR REPLACE MACRO to_integer(field) AS 
    CAST(field AS INTEGER);

CREATE OR REPLACE MACRO normalize_boolean(field) AS 
    CASE 
        WHEN field IS NULL THEN FALSE
        WHEN LOWER(field) = 'true' THEN TRUE
        WHEN field = '1' THEN TRUE
        ELSE FALSE
    END;

-- =====================================================
-- STRING MANIPULATION
-- =====================================================

CREATE OR REPLACE MACRO semicolon_to_pipe(field) AS 
    replace(field, '; ', '|');

CREATE OR REPLACE MACRO normalize_complex_members(field) AS 
    CASE 
        WHEN field IS NULL THEN NULL
        ELSE replace(replace(field, '(', ':'), ')', '')
    END;

CREATE OR REPLACE MACRO combine_gene_names(primary_field, synonym_field) AS 
    CASE 
        WHEN primary_field IS NULL AND synonym_field IS NULL THEN NULL
        WHEN primary_field IS NULL THEN replace(synonym_field, '; ', '|')
        WHEN synonym_field IS NULL THEN primary_field
        ELSE primary_field || '|' || replace(synonym_field, '; ', '|')
    END;

CREATE OR REPLACE MACRO combine_cv_terms(term1, term2) AS 
    CASE 
        WHEN term1 IS NULL AND term2 IS NULL THEN NULL
        WHEN term1 IS NULL THEN replace(term2, '; ', '|')
        WHEN term2 IS NULL THEN replace(term1, '; ', '|')
        ELSE replace(term1, '; ', '|') || '|' || replace(term2, '; ', '|')
    END;

CREATE OR REPLACE MACRO combine_alt_identifiers(primary_field, gene_synonym, protein_name, biogrid, string_ids, ensembl, kegg) AS 
    CONCAT_WS('|',
        CASE WHEN primary_field IS NOT NULL AND primary_field != '' THEN primary_field ELSE NULL END,
        CASE WHEN gene_synonym IS NOT NULL AND gene_synonym != '' THEN replace(gene_synonym, '; ', '|') ELSE NULL END,
        CASE WHEN protein_name IS NOT NULL AND protein_name != '' THEN protein_name ELSE NULL END,
        CASE WHEN biogrid IS NOT NULL AND biogrid != '' THEN biogrid ELSE NULL END,
        CASE WHEN string_ids IS NOT NULL AND string_ids != '' THEN string_ids ELSE NULL END,
        CASE WHEN ensembl IS NOT NULL AND ensembl != '' THEN ensembl ELSE NULL END,
        CASE WHEN kegg IS NOT NULL AND kegg != '' THEN kegg ELSE NULL END
    );

CREATE OR REPLACE MACRO parse_signor_members(field) AS 
    CASE 
        WHEN field IS NULL THEN NULL
        ELSE replace(replace(field, ', ', '|'), ' ', '')
    END;

CREATE OR REPLACE MACRO format_alt_identifier(name_field, id_type) AS 
    CASE 
        WHEN name_field IS NULL THEN NULL
        ELSE name_field || ':' || id_type
    END;

-- =====================================================
-- TEXT CLEANING FUNCTIONS
-- =====================================================

CREATE OR REPLACE MACRO clean_go_annotations(field) AS 
    CASE 
        WHEN field IS NULL THEN NULL
        ELSE regexp_replace(field, '\([^)]*\)', '', 'g')
    END;

CREATE OR REPLACE MACRO clean_function_text(field) AS 
    CASE
        WHEN field IS NULL THEN NULL
        WHEN field LIKE 'FUNCTION: %' THEN
            regexp_replace(
                substr(field, 11),
                ' \{[^}]+\}\.?$',
                '',
                'g'
            )
        ELSE field
    END;

CREATE OR REPLACE MACRO extract_evidence_sentence(field) AS 
    CASE 
        WHEN field LIKE 'comment:"%"' THEN 
            substr(field, 10, length(field) - 10)
        ELSE field
    END;

-- =====================================================
-- PATTERN EXTRACTION
-- =====================================================

CREATE OR REPLACE MACRO extract_pubmed_refs(field) AS 
    CASE 
        WHEN field IS NULL THEN NULL
        WHEN field LIKE '%PMID:%' THEN 
            regexp_replace(
                regexp_replace(field, '.*?(PMID:\d+).*?', '\1|', 'g'),
                '\|$', '', 'g'
            )
        ELSE NULL
    END;

CREATE OR REPLACE MACRO extract_mi_term(field) AS 
    regexp_extract(field, 'MI:[0-9]{4}');

CREATE OR REPLACE MACRO extract_accession(field) AS 
    split_part(field, ':', 2);

CREATE OR REPLACE MACRO extract_clean_accession(field) AS 
    CASE 
        WHEN field IS NULL THEN NULL
        WHEN field LIKE '%:%' THEN 
            trim(split_part(field, ':', 2))
        ELSE trim(field)
    END;

CREATE OR REPLACE MACRO extract_ebi_identifier(field) AS 
    regexp_extract(field, 'EBI-[0-9]+');

CREATE OR REPLACE MACRO extract_pubmed_id(field) AS 
    regexp_extract(field, '[0-9]+');

CREATE OR REPLACE MACRO extract_tax_id(field) AS 
    regexp_extract(field, '[0-9]+');

CREATE OR REPLACE MACRO extract_complex_members(field) AS 
    CASE 
        WHEN field IS NULL THEN NULL
        ELSE regexp_replace(field, '\([0-9]+\)', '', 'g')
    END;

-- =====================================================
-- COUNTING AND AGGREGATION
-- =====================================================

CREATE OR REPLACE MACRO count_complex_members(field) AS 
    CASE 
        WHEN field IS NULL THEN 0
        ELSE array_length(string_to_array(field, '|'), 1)
    END;

-- =====================================================
-- NORMALIZATION FUNCTIONS
-- =====================================================

CREATE OR REPLACE MACRO normalize_namespace(field) AS 
    CASE 
        WHEN field = 'molecular_function' THEN 'gene_ontology'
        WHEN field = 'biological_process' THEN 'gene_ontology'
        WHEN field = 'cellular_component' THEN 'gene_ontology'
        WHEN field = 'PSI-MI' THEN 'psi_mi'
        WHEN field = 'UniProtKB-KW' THEN 'uniprot_keywords'
        WHEN field LIKE 'omnipath%' THEN LOWER(field)
        ELSE LOWER(REPLACE(field, ' ', '_'))
    END;

-- =====================================================
-- TYPE INFERENCE FUNCTIONS
-- =====================================================

CREATE OR REPLACE MACRO infer_namespace_from_accession(field) AS 
    CASE 
        WHEN field LIKE 'GO:%' THEN 'gene_ontology'
        WHEN field LIKE 'MI:%' THEN 'psi_mi'
        WHEN field LIKE 'KW-%' THEN 'uniprot_keywords'
        WHEN field LIKE 'OM%' THEN 'omnipath'
        ELSE 'unknown'
    END;

CREATE OR REPLACE MACRO infer_signor_entity_type(field) AS 
    CASE 
        WHEN field LIKE 'SIGNOR-C%' THEN 'MI:0314'
        WHEN field LIKE 'SIGNOR-PF%' THEN 'protein_family' 
        WHEN field LIKE 'SIGNOR-PH%' THEN 'phenotype'
        WHEN field LIKE 'SIGNOR-ST%' THEN 'stimulus'
        ELSE NULL
    END;

CREATE OR REPLACE MACRO infer_entity_type_from_id(entity_field) AS 
    CASE 
        WHEN entity_field ~ '^\d+$' THEN 'MI:0250'
        WHEN entity_field LIKE 'entrez gene/locuslink:%' THEN 'MI:0250'
        WHEN entity_field LIKE 'chebi:%' OR entity_field LIKE 'CHEBI:%' THEN 'MI:0328'
        WHEN entity_field LIKE 'pubchem:%' THEN 'MI:0328'
        ELSE 'MI:0250'
    END;

-- =====================================================
-- CONTROLLED VOCABULARY MAPPINGS
-- =====================================================

CREATE OR REPLACE MACRO go_namespace_to_category(field) AS 
    CASE field
        WHEN 'biological_process' THEN 'GO:0008150'
        WHEN 'cellular_component' THEN 'GO:0005575'
        WHEN 'molecular_function' THEN 'GO:0003674'
        ELSE NULL
    END;

CREATE OR REPLACE MACRO map_entity_type_to_mi(field) AS 
    CASE LOWER(field)
        WHEN 'protein' THEN 'MI:0326'
        WHEN 'complex' THEN 'MI:0314'
        WHEN 'small molecule' THEN 'MI:0328'
        WHEN 'gene' THEN 'MI:0250'
        WHEN 'protein_family' THEN 'OM00014'
        WHEN 'phenotype' THEN 'OM00012'
        WHEN 'stimulus' THEN 'OM00013'
        ELSE NULL
    END;

CREATE OR REPLACE MACRO map_identifier_type_to_om(field) AS 
    CASE LOWER(field)
        WHEN 'uniprot' THEN 'OM00015'
        WHEN 'chebi' THEN 'OM00016'
        WHEN 'entrez' THEN 'OM00017'
        WHEN 'pubchem' THEN 'OM00018'
        WHEN 'signor' THEN 'OM00019'
        WHEN 'complexportal' THEN 'OM00020'
        WHEN 'hgnc_id' THEN 'OM00022'
        WHEN 'ncbi_gene' THEN 'OM00023'
        WHEN 'ensembl_gene' THEN 'OM00024'
        WHEN 'refseq' THEN 'OM00025'
        WHEN 'gene_symbol' THEN 'OM00026'
        WHEN 'gene_symbol_previous' THEN 'OM00027'
        WHEN 'gene_symbol_alias' THEN 'OM00028'
        ELSE 'OM00015'
    END;

CREATE OR REPLACE MACRO map_database_to_om_term(field) AS 
    CASE 
        WHEN LOWER(split_part(field, ':', 1)) LIKE 'uniprotkb%' THEN 'OM00015'
        WHEN LOWER(split_part(field, ':', 1)) LIKE 'uniprot%' THEN 'OM00015'
        WHEN LOWER(split_part(field, ':', 1)) = 'chebi' THEN 'OM00016'
        WHEN LOWER(split_part(field, ':', 1)) LIKE 'entrez%' THEN 'OM00023'
        WHEN LOWER(split_part(field, ':', 1)) = 'ncbi gene' THEN 'OM00023'
        WHEN LOWER(split_part(field, ':', 1)) = 'pubchem' THEN 'OM00018'
        WHEN LOWER(split_part(field, ':', 1)) = 'signor' THEN 'OM00019'
        WHEN LOWER(split_part(field, ':', 1)) = 'complexportal' THEN 'OM00020'
        WHEN LOWER(split_part(field, ':', 1)) = 'complex portal' THEN 'OM00020'
        WHEN LOWER(split_part(field, ':', 1)) = 'hgnc' THEN 'OM00022'
        WHEN LOWER(split_part(field, ':', 1)) LIKE 'ensembl%' THEN 'OM00024'
        WHEN LOWER(split_part(field, ':', 1)) = 'refseq' THEN 'OM00025'
        WHEN LOWER(split_part(field, ':', 1)) = 'intact' THEN 'OM00015'
        WHEN LOWER(split_part(field, ':', 1)) = 'biogrid' THEN 'OM00015'
        WHEN LOWER(split_part(field, ':', 1)) = 'string' THEN 'OM00015'
        WHEN LOWER(split_part(field, ':', 1)) = 'dip' THEN 'OM00015'
        ELSE 'OM00015'
    END;

CREATE OR REPLACE MACRO map_identifier_type_to_mi(field) AS 
    CASE 
        WHEN split_part(field, ':', 1) LIKE 'uniprotkb%' THEN 'MI:0486'
        WHEN split_part(field, ':', 1) LIKE 'uniprot%' THEN 'MI:0486'
        WHEN split_part(field, ':', 1) = 'chebi' THEN 'MI:0474'
        WHEN split_part(field, ':', 1) = 'pubchem' THEN 'MI:0730'
        WHEN split_part(field, ':', 1) = 'ensembl' THEN 'MI:0476'
        WHEN split_part(field, ':', 1) = 'entrez gene/locuslink' THEN 'MI:0477'
        WHEN split_part(field, ':', 1) = 'refseq' THEN 'MI:0481'
        WHEN split_part(field, ':', 1) = 'complexportal' THEN 'MI:1332'
        WHEN split_part(field, ':', 1) = 'signor' THEN 'MI:2214'
        WHEN split_part(field, ':', 1) = 'intact' THEN 'MI:0469'
        WHEN split_part(field, ':', 1) = 'biogrid' THEN 'MI:0463'
        WHEN split_part(field, ':', 1) = 'string' THEN 'MI:1014'
        WHEN split_part(field, ':', 1) = 'dip' THEN 'MI:0465'
        ELSE NULL
    END;

CREATE OR REPLACE MACRO map_datasource_to_mi(field) AS 
    CASE LOWER(field)
        WHEN 'signor' THEN 'MI:2214'
        WHEN 'biogrid' THEN 'MI:0463'
        WHEN 'intact' THEN 'MI:0471'
        WHEN 'string' THEN 'MI:0484'
        WHEN 'reactome' THEN 'MI:0467'
        WHEN 'dip' THEN 'MI:0465'
        WHEN 'mint' THEN 'MI:0469'
        ELSE 'MI:0686'
    END;

-- =====================================================
-- SPECIALIZED CONSTANT FUNCTIONS
-- =====================================================

CREATE OR REPLACE MACRO infer_namespace_constant(field, namespace) AS 
    namespace;

CREATE OR REPLACE MACRO constant_entity_type(field, entity_type) AS 
    CASE LOWER(entity_type)
        WHEN 'protein' THEN 'MI:0326'
        WHEN 'complex' THEN 'MI:0314'
        WHEN 'small molecule' THEN 'MI:0328'
        WHEN 'gene' THEN 'MI:0250'
        WHEN 'protein_family' THEN 'OM00014'
        WHEN 'phenotype' THEN 'OM00012'
        WHEN 'stimulus' THEN 'OM00013'
        ELSE NULL
    END;

CREATE OR REPLACE MACRO constant_identifier_type(field, id_type) AS 
    CASE LOWER(id_type)
        WHEN 'uniprot' THEN 'OM00015'
        WHEN 'chebi' THEN 'OM00016'
        WHEN 'entrez' THEN 'OM00017'
        WHEN 'pubchem' THEN 'OM00018'
        WHEN 'signor' THEN 'OM00019'
        WHEN 'complexportal' THEN 'OM00020'
        WHEN 'hgnc_id' THEN 'OM00022'
        WHEN 'ncbi_gene' THEN 'OM00023'
        WHEN 'ensembl_gene' THEN 'OM00024'
        WHEN 'refseq' THEN 'OM00025'
        WHEN 'gene_symbol' THEN 'OM00026'
        WHEN 'gene_symbol_previous' THEN 'OM00027'
        WHEN 'gene_symbol_alias' THEN 'OM00028'
        ELSE 'OM00015'
    END;

-- =====================================================
-- HGNC-SPECIFIC FUNCTIONS
-- =====================================================

CREATE OR REPLACE MACRO format_hgnc_id(field) AS 
    CASE 
        WHEN field IS NULL THEN NULL
        WHEN field LIKE 'HGNC:%' THEN field
        ELSE 'HGNC:' || field
    END;

CREATE OR REPLACE MACRO format_hgnc_id_with_prefix(field) AS 
    CASE 
        WHEN field IS NULL THEN NULL
        WHEN field LIKE 'HGNC:%' THEN 'OM00022:' || replace(field, 'HGNC:', '')
        ELSE 'OM00022:' || field
    END;

CREATE OR REPLACE MACRO extract_primary_id(field) AS 
    CASE 
        WHEN field IS NULL THEN NULL
        WHEN field LIKE '%|%' THEN split_part(field, '|', 1)
        ELSE field
    END;

CREATE OR REPLACE MACRO split_pipe_separated(field) AS 
    CASE 
        WHEN field IS NULL THEN NULL
        WHEN field = '' THEN NULL
        ELSE field
    END;

CREATE OR REPLACE MACRO combine_hgnc_identifiers(field, uniprot_field, ncbi_gene_field, ensembl_field, refseq_field, previous_symbols_field, alias_symbols_field) AS 
    CONCAT_WS('|',
        CASE WHEN field IS NOT NULL AND field != '' 
            THEN 'OM00026:' || field ELSE NULL END,
        CASE WHEN uniprot_field IS NOT NULL AND uniprot_field != '' 
            THEN 'OM00015:' || replace(uniprot_field, ', ', '|OM00015:') ELSE NULL END,
        CASE WHEN ncbi_gene_field IS NOT NULL AND ncbi_gene_field != '' 
            THEN 'OM00023:' || ncbi_gene_field ELSE NULL END,
        CASE WHEN ensembl_field IS NOT NULL AND ensembl_field != '' 
            THEN 'OM00024:' || ensembl_field ELSE NULL END,
        CASE WHEN refseq_field IS NOT NULL AND refseq_field != '' 
            THEN 'OM00025:' || refseq_field ELSE NULL END,
        CASE WHEN previous_symbols_field IS NOT NULL AND previous_symbols_field != '' 
            THEN 'OM00027:' || replace(previous_symbols_field, ', ', '|OM00027:') ELSE NULL END,
        CASE WHEN alias_symbols_field IS NOT NULL AND alias_symbols_field != '' 
            THEN 'OM00028:' || replace(alias_symbols_field, ', ', '|OM00028:') ELSE NULL END
    );

CREATE OR REPLACE MACRO split_comma_separated_values(field) AS 
    CASE 
        WHEN field IS NULL OR field = '' THEN NULL
        ELSE trim(field)
    END;
