import type { MeilisearchFilters } from "@/types/meilisearch";

export function buildEntityFilterString(filters: MeilisearchFilters): string {
  const filterParts: string[] = [];

  if (filters.entity_ids?.length) {
    const ids = filters.entity_ids.join(", ");
    filterParts.push(`entity_id IN [${ids}]`);
  }

  if (filters.entity_types?.length) {
    const entityTypeFilters = filters.entity_types.map(type => `entity_type = "${type}"`).join(" OR ");
    filterParts.push(`(${entityTypeFilters})`);
  }

  if (filters.sources?.length) {
    const sourceFilters = filters.sources.map(source => `sources = "${source}"`).join(" OR ");
    filterParts.push(`(${sourceFilters})`);
  }

  if (filters.ncbi_tax_id?.length) {
    const taxIdFilters = filters.ncbi_tax_id.map(taxId => `ncbi_tax_id = "${taxId}"`).join(" OR ");
    filterParts.push(`(${taxIdFilters} OR ncbi_tax_id IS NULL)`);
  }

  if (filters.cv_terms_go?.length) {
    const cvTermFilters = filters.cv_terms_go.map(term => `cv_terms_go = "${term}"`).join(" OR ");
    filterParts.push(`(${cvTermFilters})`);
  }
  if (filters.cv_terms_mi?.length) {
    const cvTermFilters = filters.cv_terms_mi.map(term => `cv_terms_mi = "${term}"`).join(" OR ");
    filterParts.push(`(${cvTermFilters})`);
  }
  if (filters.cv_terms_om?.length) {
    const cvTermFilters = filters.cv_terms_om.map(term => `cv_terms_om = "${term}"`).join(" OR ");
    filterParts.push(`(${cvTermFilters})`);
  }
  if (filters.cv_terms_hp?.length) {
    const cvTermFilters = filters.cv_terms_hp.map(term => `cv_terms_hp = "${term}"`).join(" OR ");
    filterParts.push(`(${cvTermFilters})`);
  }
  if (filters.cv_terms_kw?.length) {
    const cvTermFilters = filters.cv_terms_kw.map(term => `cv_terms_kw = "${term}"`).join(" OR ");
    filterParts.push(`(${cvTermFilters})`);
  }

  return filterParts.join(" AND ");
}

export function buildInteractionFilterString(filters: MeilisearchFilters): string {
  const filterParts: string[] = [];

  if (filters.entity_ids?.length) {
    const entityFilters = filters.entity_ids
      .map((id) => `(member_a_id = ${id} OR member_b_id = ${id})`)
      .join(" OR ");
    filterParts.push(`(${entityFilters})`);
  }

  if (filters.member_a_id !== undefined) {
    filterParts.push(`(member_a_id = ${filters.member_a_id} OR member_b_id = ${filters.member_a_id})`);
  }

  if (filters.member_b_id !== undefined) {
    filterParts.push(`(member_a_id = ${filters.member_b_id} OR member_b_id = ${filters.member_b_id})`);
  }

  if (filters.member_types?.length) {
    const typeFilters = filters.member_types.map((type) => `member_types = "${type}"`).join(" OR ");
    filterParts.push(`(${typeFilters})`);
  }

  if (filters.has_direction !== undefined && filters.has_direction !== null) {
    filterParts.push(`has_direction = ${filters.has_direction}`);
  }

  if (filters.has_positive_sign !== undefined && filters.has_positive_sign !== null) {
    filterParts.push(`has_positive_sign = ${filters.has_positive_sign}`);
  }

  if (filters.has_negative_sign !== undefined && filters.has_negative_sign !== null) {
    filterParts.push(`has_negative_sign = ${filters.has_negative_sign}`);
  }

  if (filters.interaction_annotation_terms?.length) {
    const termFilters = filters.interaction_annotation_terms
      .map((term) => `interaction_annotation_terms = "${term}"`)
      .join(" OR ");
    filterParts.push(`(${termFilters})`);
  }

  if (filters.sources?.length) {
    const sourceFilters = filters.sources.map((source) => `sources = "${source}"`).join(" OR ");
    filterParts.push(`(${sourceFilters})`);
  }

  return filterParts.join(" AND ");
}
