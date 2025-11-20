import polars as pl
from pypath.internals.cv_terms.annotations import (
    BiologicalEffectCv,
    CausalMechanismCv,
    CausalStatementCv,
    BiologicalRoleCv,
    PharmacologicalActionCv,
)

# Read files
ent_id = pl.read_parquet('databases/omnipath/output/entity_identifier.parquet')
mem_ann = pl.read_parquet('databases/omnipath/output/membership_annotation.parquet')
membership = pl.read_parquet('databases/omnipath/output/membership.parquet')

# Get NAME identifier namespace entity_id (OM:0202)
name_namespace_eid = ent_id.filter(pl.col('identifier') == 'OM:0202')['entity_id'][0]

# Get all causal-related CV term accessions
causal_accessions = []
causal_categories = {}

for term in CausalStatementCv:
    causal_accessions.append(term.value)
    causal_categories[term.value] = 'CausalStatement'

for term in CausalMechanismCv:
    causal_accessions.append(term.value)
    causal_categories[term.value] = 'CausalMechanism'

for term in BiologicalEffectCv:
    causal_accessions.append(term.value)
    causal_categories[term.value] = 'BiologicalEffect'

for term in BiologicalRoleCv:
    causal_accessions.append(term.value)
    causal_categories[term.value] = 'BiologicalRole'

for term in PharmacologicalActionCv:
    causal_accessions.append(term.value)
    causal_categories[term.value] = 'PharmacologicalAction'

print('=== CAUSAL ANNOTATION STATISTICS ===')
print(f'Total CV terms searched: {len(causal_accessions)}')
print()

# Map accessions to entity_ids and get names
causal_entity_ids = []
eid_to_info = {}
for acc in causal_accessions:
    matches = ent_id.filter(pl.col('identifier') == acc)
    if len(matches) > 0:
        eid = matches['entity_id'][0]
        causal_entity_ids.append(eid)
        
        # Get the name for this entity
        name_matches = ent_id.filter(
            (pl.col('entity_id') == eid) &
            (pl.col('type_id') == name_namespace_eid)
        )
        name = name_matches['identifier'][0] if len(name_matches) > 0 else acc
        
        eid_to_info[eid] = {
            'accession': acc,
            'name': name,
            'category': causal_categories[acc]
        }

print(f'Found {len(causal_entity_ids)} causal/regulatory terms in the database')
print()

# Count MEMBER annotations
print('=' * 85)
print('MEMBER ANNOTATIONS (membership_annotation table)')
print('=' * 85)
causal_mem_ann = mem_ann.filter(pl.col('annotation_id').is_in(causal_entity_ids))
print(f'Total causal member annotations: {len(causal_mem_ann):,}')
print()

if len(causal_mem_ann) > 0:
    mem_ann_counts = (
        causal_mem_ann
        .group_by('annotation_id')
        .agg(pl.len().alias('count'))
        .sort('count', descending=True)
    )
    
    print(f'{"Accession":<12} {"Category":<22} {"Name":<40} {"Count":>10}')
    print('-' * 85)
    for row in mem_ann_counts.iter_rows(named=True):
        info = eid_to_info[row['annotation_id']]
        print(f'{info["accession"]:<12} {info["category"]:<22} {info["name"]:<40} {row["count"]:>10,}')
    
    # Category summary for member annotations
    print()
    mem_category_counts = {}
    for row in mem_ann_counts.iter_rows(named=True):
        info = eid_to_info[row['annotation_id']]
        category = info['category']
        mem_category_counts[category] = mem_category_counts.get(category, 0) + row['count']
    
    print('Member annotation summary by category:')
    for category in sorted(mem_category_counts.keys()):
        print(f'  {category:<25} {mem_category_counts[category]:>10,}')
else:
    print('(No member-level causal annotations found)')

print()
print('=' * 85)
print('INTERACTION-LEVEL ANNOTATIONS (membership table)')
print('=' * 85)
causal_interactions = membership.filter(pl.col('parent_id').is_in(causal_entity_ids))
print(f'Total causal interaction-level annotations: {len(causal_interactions):,}')
print()

if len(causal_interactions) > 0:
    interaction_counts = (
        causal_interactions
        .group_by('parent_id')
        .agg(pl.len().alias('count'))
        .sort('count', descending=True)
    )
    
    print(f'{"Accession":<12} {"Category":<22} {"Name":<40} {"Count":>10}')
    print('-' * 85)
    for row in interaction_counts.iter_rows(named=True):
        info = eid_to_info[row['parent_id']]
        print(f'{info["accession"]:<12} {info["category"]:<22} {info["name"]:<40} {row["count"]:>10,}')
    print()
    int_category_counts = {}
    for row in interaction_counts.iter_rows(named=True):
        info = eid_to_info[row['parent_id']]
        category = info['category']
        int_category_counts[category] = int_category_counts.get(category, 0) + row['count']
    
    print('Interaction annotation summary by category:')
    for category in sorted(int_category_counts.keys()):
        print(f'  {category:<25} {int_category_counts[category]:>10,}')

print()
print('=' * 85)
print('OVERALL SUMMARY')
print('=' * 85)
print(f'Member-level annotations:      {len(causal_mem_ann):>10,}')
print(f'Interaction-level annotations: {len(causal_interactions):>10,}')
print(f'TOTAL annotations:             {len(causal_mem_ann) + len(causal_interactions):>10,}')
print('=' * 85)