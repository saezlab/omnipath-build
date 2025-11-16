# OmniPath Next.js Frontend

This is the Next.js frontend application for OmniPath 2.0, a comprehensive molecular interaction database and analysis platform.

## Architecture

The application directly connects to:
- **PostgreSQL** - For structured data queries (entities, interactions, metadata)
- **Meilisearch** - For full-text search and faceted filtering
- **AI Integration** - Chat interface with SQL query capabilities

## Key Features

- **Entity Search** - Find proteins, genes, and complexes
- **Interaction Analysis** - Explore molecular interactions with evidence
- **Network Visualization** - Graph-based interaction networks
- **AI Assistant** - Natural language queries with SQL execution
- **Enrichment Analysis** - Statistical pathway and term enrichment

## Getting Started

### Prerequisites

1. PostgreSQL database with OmniPath gold-layer tables
2. Meilisearch instance with indexed data
3. Environment variables configured

### Environment Variables

Create a `.env.local` file:

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/omnipath

# Meilisearch
MEILISEARCH_MASTER_KEY=your_master_key
# or
MEILISEARCH_API_KEY=your_api_key

# Application
NEXT_PUBLIC_DOMAIN=localhost
NEXT_PUBLIC_ENVIRONMENT=development
DOCKERIZED=false
```

### Development Server

```bash
npm install
npm run dev
# or
yarn install
yarn dev
# or
pnpm install
pnpm dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser.

## Database Requirements

The application expects the following PostgreSQL tables in the `gold` schema:

### Core Tables
- `gold.entity` - Biological entities (proteins, genes, complexes)
- `gold.entity_identifier` - Alternative identifiers for entities
- `gold.interaction` - Molecular interactions
- `gold.interaction_evidence` - Supporting evidence for interactions
- `gold.cv_term` - Controlled vocabulary terms
- `gold.entity_cv_term` - Entity-term associations

### Reference Tables
- `gold.entity_type` - Entity type definitions
- `gold.interaction_type` - Interaction type definitions
- `gold.data_source` - Data source information
- `gold.detection_method` - Experimental methods
- `gold.reference` - Publications and citations

## Meilisearch Indexes

The application requires three Meilisearch indexes:

1. **entities** - Entity search with facets
2. **cv_terms** - Controlled vocabulary search
3. **interactions** - Interaction search with complex filtering

## API Structure

### Database Queries (`/lib/database/`)
- **entities.ts** - Entity retrieval and search
- **interactions.ts** - Interaction queries and evidence
- **network.ts** - Network analysis functions
- **enrichment.ts** - Statistical enrichment analysis
- **query-executor.ts** - Safe SQL query execution

### Meilisearch (`/lib/meilisearch/`)
- **client.ts** - Meilisearch client configuration
- **search.ts** - Search functions with filtering

## Migration Notes

This application has been migrated from Django API dependency to direct database access. See `MIGRATION_SUMMARY.md` for detailed information about the changes.

## Development

### Project Structure
```
src/
├── app/                 # Next.js app router pages
├── components/          # Reusable UI components
├── features/           # Feature-specific components
├── hooks/              # Custom React hooks
├── lib/                # Core business logic
│   ├── database/       # Database query functions
│   ├── meilisearch/    # Search functionality
│   └── api.ts          # Public API interface
└── types/              # TypeScript type definitions
```

### Adding New Features

1. Database queries go in `/lib/database/`
2. Search functionality uses `/lib/meilisearch/`
3. Update `/lib/api.ts` for public interfaces
4. Add components in `/features/` for new pages

## Deployment

For production deployment:

1. Set appropriate environment variables
2. Ensure PostgreSQL and Meilisearch are accessible
3. Configure `DOCKERIZED=true` for container environments
4. Set `NEXT_PUBLIC_ENVIRONMENT=production`

## Learn More

- [Next.js Documentation](https://nextjs.org/docs)
- [Drizzle ORM](https://orm.drizzle.team/) - Database ORM used
- [Meilisearch](https://docs.meilisearch.com/) - Search engine
- [Tailwind CSS](https://tailwindcss.com/) - Styling framework
