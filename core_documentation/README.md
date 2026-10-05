# Core documentation

A short, current description of the core of OmniPath build: what the data
means, how entities are resolved, how PostgreSQL and the subsets are built,
and the decisions behind them.

| Page | Read it to learn |
| --- | --- |
| [Overview](overview.md) | The stages, versions and package ownership |
| [Entities and relations](entities.md) | What each Parquet row and field means |
| [Entity resolution](resolution.md) | How identifiers become entities, and when we abstain |
| [PostgreSQL](postgres.md) | How a release is loaded and mapped to tables |
| [Subsets](subsets.md) | MetSigDB, network views and COSMOS |
| [Decisions](decisions.md) | What we chose and why |
| [Glossary](glossary.md) | The terms we use |

## Reading it

The Markdown files are the source and render directly on GitHub. For the HTML
viewer with navigation, search and glossary pop-ups, run:

```bash
make docs
```

and open <http://127.0.0.1:8090>.

## Keeping it current

- These pages describe the system as it is now. History, experiments and dated
  measurements stay in [`docs/`](../docs/README.md).
- A pull request that changes a schema, a resolution rule, the PostgreSQL
  mapping or a product rule updates the matching page here.
- A changed decision is edited in place in [decisions.md](decisions.md) with
  its new date; a new term goes into [glossary.md](glossary.md).
- Keep each page short. Link to package READMEs for operational detail.
