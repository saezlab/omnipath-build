'use client';

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { DatabaseInfo, SourceInfo } from '../app/lib/database-scanner';

interface DatabaseContextType {
  databases: DatabaseInfo[];
  sources: SourceInfo[];
  selectedDatabase: DatabaseInfo | null;
  setSelectedDatabase: (database: DatabaseInfo | null) => void;
  loading: boolean;
  error: string | null;
  refreshDatabases: () => Promise<void>;
}

const DatabaseContext = createContext<DatabaseContextType | undefined>(undefined);

export function DatabaseProvider({ children }: { children: ReactNode }) {
  const [databases, setDatabases] = useState<DatabaseInfo[]>([]);
  const [sources, setSources] = useState<SourceInfo[]>([]);
  const [selectedDatabase, setSelectedDatabase] = useState<DatabaseInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDatabases = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch('/api/sources');
      if (!response.ok) throw new Error('Failed to fetch databases');

      const data = await response.json();
      setDatabases([data.database]);
      setSources(data.sources || []);

      // Auto-select omnipath database
      if (!selectedDatabase && data.database) {
        setSelectedDatabase(data.database);
      }
    } catch (error) {
      console.error('Error fetching databases:', error);
      setError(error instanceof Error ? error.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDatabases();
  }, []);

  const refreshDatabases = async () => {
    await fetchDatabases();
  };

  return (
    <DatabaseContext.Provider
      value={{
        databases,
        sources,
        selectedDatabase,
        setSelectedDatabase,
        loading,
        error,
        refreshDatabases,
      }}
    >
      {children}
    </DatabaseContext.Provider>
  );
}

export function useDatabase() {
  const context = useContext(DatabaseContext);
  if (context === undefined) {
    throw new Error('useDatabase must be used within a DatabaseProvider');
  }
  return context;
}
