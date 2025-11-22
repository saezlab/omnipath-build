/**
 * Simplified configuration - removed Django dependencies
 * Now only handles Meilisearch and frontend URLs
 */

// Core configuration from environment
const ENVIRONMENT = process.env.NEXT_PUBLIC_ENVIRONMENT || process.env.NODE_ENV || 'development';
const DOMAIN = process.env.NEXT_PUBLIC_DOMAIN || 'localhost';
const IS_PRODUCTION = ENVIRONMENT === 'production';
const IS_DOCKERIZED = process.env.DOCKERIZED === 'true';

// Derive protocol
const PROTOCOL = IS_PRODUCTION ? 'https' : 'http';

// Derive all URLs automatically
const API_CONFIG = {
  // Frontend URL
  siteUrl: IS_PRODUCTION
    ? `${PROTOCOL}://${DOMAIN}`
    : `${PROTOCOL}://${DOMAIN}:3000`,
  
  // Meilisearch URL
  meilisearchUrl: IS_DOCKERIZED
    ? 'http://omnipath-meilisearch:7700'
    : 'http://localhost:7700',

  // Entity service URL (identifier lookup)
  entityServiceUrl: process.env.NEXT_PUBLIC_ENTITY_SERVICE_URL || 'http://localhost:8080',

  // PostgreSQL connection (handled by DATABASE_URL environment variable)
  databaseUrl: process.env.DATABASE_URL || 'postgresql://localhost:5432/omnipath',
};


/**
 * Get the site URL (frontend URL)
 */
export const getSiteUrl = (): string => {
  return API_CONFIG.siteUrl;
};

/**
 * Get the Meilisearch URL
 */
export const getMeilisearchUrl = (): string => {
  return API_CONFIG.meilisearchUrl;
};

/**
 * Get the entity service URL (identifier lookup backend)
 */
export const getEntityServiceUrl = (): string => {
  return API_CONFIG.entityServiceUrl;
};

/**
 * Get the database URL
 */
export const getDatabaseUrl = (): string => {
  return API_CONFIG.databaseUrl;
};

/**
 * Check if we're in development mode
 */
export const isDevelopment = (): boolean => {
  return !IS_PRODUCTION;
};

// Debug logging in development
if (isDevelopment() && typeof window !== 'undefined') {
  console.log('API Configuration:', {
    environment: ENVIRONMENT,
    domain: DOMAIN,
    siteUrl: API_CONFIG.siteUrl,
    meilisearchUrl: API_CONFIG.meilisearchUrl,
  });
}

export default API_CONFIG;
