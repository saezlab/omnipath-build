# Runtime matching policy

`LibraryMatcher` normalizes observation evidence, encodes lookup keys, reads
identifier postings, applies the native biological decision policy, and enriches
accepted matches from complete stored entity records. `EntityResolver` exposes
stable scalar and multiple-target results and tracks resolution statistics.

Each matcher pins one complete immutable reference generation. Its two indexes
map normalized keys to candidate decision metadata and entity IDs to taxon, label
and identifier aliases. Runtime reads only required records and never imports
reference construction or replay commands.

Reference construction and atomic publication are owned by
`omnipath_build.canonical.library` and `omnipath_build.reference`; see the
[reference build guide](../../../build/REFERENCE.md). Shared identifier
normalization, namespace encoding and disk codecs have one implementation in
resolver and are reused by the compiler.
