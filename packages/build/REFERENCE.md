# Identity layer and resolver

Resource builds resolve against an identity library (`omnipath-identity-v2`),
built offline from one set of hubs. Each hub is indexed on its own, then the
identity decisions (lipid-name anchors, one-step attachment, grouping, quarantine,
gene–product links) are computed once for all hubs. Rules and decisions are
described in [entity resolution](../../core_documentation/resolution.md); the
implementation spec is [docs/identity-layer-spec.md](../../docs/identity-layer-spec.md).

```sh
export PYPATH_DOWNLOAD_DATADIR="$PWD/data/pypath-data"
omnipath-build export-hubs --output-dir data/reference/hubs --max-records 0 --no-library
# Per hub: the rule-independent index and its LMDB point-lookup store.
omnipath-build build-hub-index --hub chebi --hubs-dir data/reference/hubs --output-root data/reference/hub-index
omnipath-build build-hub-kv --hub chebi --hub-index-root data/reference/hub-index
# Once for all hubs: the identity decisions and their LMDB store.
omnipath-build build-identity --hub-index-root data/reference/hub-index --output-dir data/reference/identity
omnipath-build build-identity-kv --identity-dir data/reference/identity/<fingerprint>
```

A hub index is rebuilt only when its hub export changes. The identity library is
named by a fingerprint of its hub indexes and rules code; a build passes it as
`--library-dir` (or `OMNIPATH_LIBRARY_DIR`) and records the fingerprint in its
provenance. There is no candidate limit: every identifier keeps all candidates,
and matching abstains when more than one survives.

Entity records are assembled on first use and cached per fingerprint in SQLite
(`OMNIPATH_IDENTITY_CACHE`); a cached record equals a freshly built one.

The previous compact reference (`build-library`, `omnipath_build.reference`) is no
longer used by resource builds. Its code remains for the regression comparison in
`omnipath_build.regression`.
