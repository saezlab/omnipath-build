# Django to PostgreSQL + Meilisearch Migration Summary

This document summarizes the migration from Django API calls to direct PostgreSQL and Meilisearch access in the Next.js application.

## Overview

The Next.js application has been migrated to bypass the Django backend and interact directly with:
- PostgreSQL database for entity, interaction, and metadata queries
- Meilisearch for full-text search functionality

## Architecture Changes

### Before Migration
```
Next.js → Django REST API → PostgreSQL/Meilisearch
```

### After Migration
```
Next.js → Direct access → PostgreSQL + Meilisearch
```

## Files Modified

### New Database Query Modules
- `/lib/database/entities.ts` - Entity queries and search
- `/lib/database/interactions.ts` - Interaction queries and search
- `/lib/database/network.ts` - Network analysis functions
- `/lib/database/query-executor.ts` - Safe SQL query execution
- `/lib/database/enrichment.ts` - Enrichment analysis
- `/lib/database/index.ts` - Re-exports all database functions

### Updated Core Files
- `/lib/api.ts` - Replaced Django API calls with direct database/Meilisearch calls
- `/lib/api/config.ts` - Removed Django URL configuration
- `/lib/meilisearch/search.ts` - Enhanced with exports for other modules
- `/app/api/[transport]/route.ts` - Updated to use direct query executor

## Key Functions Migrated

### Entity APIs
- `getEntity(id)` - Now queries PostgreSQL directly
- `searchEntities(params)` - Uses Meilisearch directly
- `resolveIdentifiers(identifiers)` - PostgreSQL identifier resolution

### Interaction APIs
- `getInteraction(id)` - PostgreSQL queries with evidence aggregation
- `searchInteractions(params)` - Meilisearch with advanced filtering
- `getInteractionEvidences(id)` - Paginated evidence retrieval

### Network APIs
- `getNetworkNeighbors(params)` - Graph traversal using SQL
- `getShortestPath(params)` - Recursive CTE for path finding

### Search APIs
- `searchMeilisearch(params)` - Direct Meilisearch client usage
- `searchInteractionsMeilisearch(params)` - Enhanced interaction search
- `fetchMeilisearchDocuments()` - Document retrieval by IDs

### Utility APIs
- `executeSQLQuery(query)` - Safe read-only SQL execution
- `performEnrichment(entityIds)` - Statistical enrichment analysis
- `checkHealth()` - Database/Meilisearch health checks

## Database Schema Requirements

The migration assumes the following gold-layer tables exist:
- `gold.entity` - Main entity table
- `gold.entity_identifier` - Entity identifiers
- `gold.interaction` - Interaction data
- `gold.interaction_evidence` - Evidence records
- `gold.cv_term` - Controlled vocabulary terms
- `gold.entity_cv_term` - Entity-term associations

## Environment Variables

Required environment variables:
- `DATABASE_URL` - PostgreSQL connection string
- `MEILISEARCH_MASTER_KEY` or `MEILISEARCH_API_KEY` - Meilisearch authentication
- `NEXT_PUBLIC_DOMAIN` - Domain for URL generation
- `DOCKERIZED` - Set to 'true' in containerized environments

## Benefits

1. **Performance**: Direct database access eliminates HTTP overhead
2. **Simplicity**: Reduced architecture complexity
3. **Flexibility**: Custom queries and optimizations possible
4. **Reliability**: Fewer network hops and dependencies

## Migration Considerations

1. **Type Safety**: Some type conversions required due to API schema differences
2. **Error Handling**: Direct database errors need proper handling
3. **Caching**: Consider implementing caching for frequently accessed data
4. **Monitoring**: Database query performance should be monitored

## Testing

After migration, test the following functionality:
- Entity search and retrieval
- Interaction search with filters
- Network analysis features
- AI chat with SQL queries
- CV term lookup
- Enrichment analysis

## Rollback Plan

If rollback is needed:
1. Revert `/lib/api.ts` to use Django API calls
2. Restore original `/lib/api/config.ts`
3. Remove new database modules
4. Update environment variables to point to Django

## Next Steps

1. Performance optimization of database queries
2. Implement caching where appropriate
3. Add comprehensive error handling
4. Monitor query performance and optimize indexes
5. Consider connection pooling for high-load scenarios