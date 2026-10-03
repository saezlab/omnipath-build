# Resource browsing catalog

`resource_tags.yaml` defines stable tag IDs, labels, dimensions and descriptions.
`resource_catalog.yaml` assigns those tags and short descriptions to resource IDs.
Both files use JSON syntax (a YAML subset), consistent with the generated source metadata.
Edit these two files to change browsing metadata centrally; deploy the updated core package
and restart the API to load changes. No resource rebuild is required.

Assignments describe the current pypath `inputs_v2` imports rather than every dataset
available on a provider's website. `curation_source` links to the input module used
for the initial assignment. Legacy pypath's content and interaction-model distinctions
inspired the vocabulary. Knowledge-origin tags mean a resource contains that kind of
knowledge, not that every imported record has that evidence. Omit uncertain tags.
Snapshot-specific dataset subsets remain separately visible in build metadata; these
resource-level tags do not promise that every subset includes all tagged content.

`resource_metadata.yaml` is generated from pypath and must not hold manual curation.
The loader first selects build metadata (or the generated fallback for legacy builds),
then adds current browsing metadata. Exact license, download time and other acquisition
fields remain from that selected metadata.

Each catalog record holds a `license_reference` and separate academic/commercial use
statuses. These are browsing classifications, subject to the exact license conditions.
`allowed` means the recorded data license permits that category of use; `not_allowed`
means it excludes it; `requires_permission` means a separate authorization is specified;
`unknown` means the available metadata does not settle the question. A noncommercial
license is not a guarantee that every use by an academic institution is noncommercial.
Software licenses alone are left unknown for imported data. Vague commercial restrictions
also stay unknown. The loader applies classifications only when the selected build's
license exactly matches `license_reference`; missing or different licenses yield unknown.
Review `license_note` and the source terms when changing these classifications.
