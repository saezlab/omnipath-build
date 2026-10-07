// UniProt core annotation categories: http://purl.uniprot.org/core/
const uniprotNarratives: Record<string, string> = {
  'up:function_annotation': 'Function',
  'up:disease_annotation': 'Disease',
  'up:subcellular_location_annotation': 'Subcellular location',
  'up:ptm_annotation': 'Post-translational modification',
  'up:pathway_annotation': 'Pathway',
  'up:activity_regulation_annotation': 'Activity regulation',
  'up:mutagenesis_annotation': 'Mutagenesis',
  'up:transmembrane_annotation': 'Transmembrane',
};

export function isNarrativeAnnotation(term: string): boolean {
  const key = term.replace(/^biolink:/i, '').toLowerCase();
  // Transmembrane notes are bare feature lists ("Helical; Helical"), not prose.
  return (
    (Object.hasOwn(uniprotNarratives, key) && key !== 'up:transmembrane_annotation') ||
    [
      'description',
      'function',
      'disease_involvement',
      'subcellular_location',
      'iao:0000115',
    ].includes(key)
  );
}

/** Shared presentation only: source semantics and stored values remain intact. */
const labels: Record<string, string> = {
  ...uniprotNarratives,
  'chemrof:mass': 'Molecular mass',
  'chemrof:monoisotopic_mass': 'Monoisotopic mass',
  'chemrof:charge': 'Charge',
  has_chemical_formula: 'Formula',
  has_biological_sequence: 'Sequence',
  lower_bound: 'Model flux lower bound',
  upper_bound: 'Model flux upper bound',
  member_content: 'Content',
  member_min: 'Minimum',
  member_max: 'Maximum',
  member_mean: 'Mean',
  member_median: 'Median',
  member_sd: 'Standard deviation',
  member_value: 'Value',
  pchembl_value: 'pChEMBL',
  ph: 'pH',
  intact_confidence_value: 'IntAct confidence',
  object_direction_qualifier: 'Effect direction',
  object_aspect_qualifier: 'Affected aspect',
  causal_mechanism_qualifier: 'Mechanism',
  source_record_urls: 'Source record',
  has_evidence_of_type: 'Evidence type',
  in_taxon: 'Organism',
  has_topic: 'Classification / reference',
  original_object: 'Source target',
  original_subject: 'Source subject',
  original_predicate: 'Source relationship',
  'biopax:cellularlocation': 'Compartment',
  'biopax:displayname': 'Participant state / name',
  'biopax:feature': 'Participant modification',
  supporting_study_method_description: 'Method / sample context',
  supporting_data_source: 'Supporting database',
  xref: 'Cross-reference',
  stoichiometry: 'Stoichiometry',
  has_quantitative_value: 'Value',
  has_confidence_score: 'Confidence score',
  chembl_confidence_score: 'ChEMBL target confidence (0–9)',
  'chembl:data_validity_comment': 'ChEMBL data validity',
  'chembl:action': 'Mechanism of action',
  p_value: 'p-value',
  pic50: 'pIC50',
  pki: 'pKi',
  pkd: 'pKd',
  pec50: 'pEC50',
  'biopax:conversiondirection': 'Reaction direction',
  'biopax:control_set': 'Controller set',
  'omnipath:gene_mapping_status': 'Gene mapping',
  'omnipath:gene_mapping_candidate': 'Gene mapping candidates',
  'omnipath:protein_mapping_status': 'Protein mapping',
  'brenda:molecular_observation': 'BRENDA observation',
  'brenda:source_protein_record': 'BRENDA protein record',
  'uniprot:catalogue_feature': 'UniProt sequence feature',
  'mirbase:precursor_region': 'Precursor region',
  'recon3d:source_product_clauses': 'Gene–reaction rule',
  'recon3d:source_product_selector': 'Gene product',
  'macdb:trait_type': 'Trait type',
  'connectomedb:participant_role': 'Participant role',
};

// has_topic holds codes of many vocabularies; the value's prefix names it.
const topicLabels: [RegExp, string][] = [
  [/^EC:/i, 'EC number'],
  [/^TC:/i, 'Transporter class'],
  [/^SBO:/i, 'SBO term'],
  [/^MI:/i, 'PSI-MI term'],
  [/^EFO:/i, 'EFO term'],
  [/^GO:/i, 'GO term'],
  [/^KEGG\.RCLASS:/i, 'KEGG reaction class'],
  [/^KEGG\.REACTION:/i, 'KEGG reaction'],
  [/^(?:KEGG\.ORTHOLOGY|KO):/i, 'KEGG orthology'],
  [/^WP\d+_r\d+$/i, 'WikiPathways revision'],
];

/** The label of an annotation, from its term and, for has_topic, its value. */
export function annotationLabelFor(term: string, value?: string): string {
  if (term.replace(/^biolink:/i, '').toLowerCase() === 'has_topic' && value) {
    const match = topicLabels.find(([pattern]) => pattern.test(value.trim()));
    if (match) return match[1];
  }
  return annotationLabel(term);
}

const defaultUnits: Record<string, string> = {
  'chemrof:mass': 'Da',
  'chemrof:monoisotopic_mass': 'Da',
};

/** The unit a term's plain-number values are given in, when the value has none. */
export function annotationDefaultUnit(term: string): string | undefined {
  return defaultUnits[term.replace(/^biolink:/i, '').toLowerCase()];
}

// Terms whose many values read better as one row.
const listedTerms = new Set(['omnipath:gene_mapping_candidate']);

/** Rows of a term in listedTerms merged into one row listing up to five values. */
export function collapseListedAnnotations<T extends { term: string; value?: string }>(
  rows: T[],
): T[] {
  const result: T[] = [];
  const merged = new Map<string, { row: T; values: string[] }>();
  for (const row of rows) {
    const key = row.term.toLowerCase();
    if (!listedTerms.has(key)) {
      result.push(row);
      continue;
    }
    const entry = merged.get(key);
    if (entry) {
      if (row.value) entry.values.push(row.value);
      continue;
    }
    const created = { row: { ...row }, values: row.value ? [row.value] : [] };
    merged.set(key, created);
    result.push(created.row);
  }
  for (const { row, values } of merged.values()) {
    const unique = [...new Set(values)];
    row.value =
      unique.slice(0, 5).join(', ') + (unique.length > 5 ? ` and ${unique.length - 5} more` : '');
  }
  return result;
}
export function annotationLabel(term: string): string {
  const key = term.replace(/^biolink:/i, '').toLowerCase();
  if (labels[key]) return labels[key];
  const label = term
    .replace(/^biolink:/i, '')
    .replace(/_/g, ' ')
    .replace(/\s*\(nM\)$/i, '');
  return label.charAt(0).toUpperCase() + label.slice(1);
}
export function plainText(value: string): string {
  // Strip actual HTML tags only. A reaction operator such as <=> is data.
  return value
    .replace(/<\/?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*?)?\s*\/?\s*>/g, '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}
export function safeSourceUrl(value?: string): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}
