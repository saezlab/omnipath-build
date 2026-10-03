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
  return (
    Object.hasOwn(uniprotNarratives, key) ||
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
};
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
