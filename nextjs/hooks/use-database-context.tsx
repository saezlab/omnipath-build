'use client';

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { DatabaseInfo } from '../app/lib/database-scanner';

interface DatabaseContextType {
  databases: DatabaseInfo[];
  selectedDatabase: DatabaseInfo | null;
  setSelectedDatabase: (database: DatabaseInfo | null) => void;
  loading: boolean;
  error: string | null;
  refreshDatabases: () => Promise<void>;
}

const DatabaseContext = createContext<DatabaseContextType | undefined>(undefined);

export function DatabaseProvider({ children }: { children: ReactNode }) {
  const [databases, setDatabases] = useState<DatabaseInfo[]>([]);
  const [selectedDatabase, setSelectedDatabase] = useState<DatabaseInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDatabases = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch('/api/databases');
      if (!response.ok) throw new Error('Failed to fetch databases');
      
      const data = await response.json();
      setDatabases(data);
      
      // Auto-select first database if none selected and databases exist
      if (!selectedDatabase && data.length > 0) {
        setSelectedDatabase(data[0]);
      }
      
      // Update selected database if it still exists in the new data
      if (selectedDatabase) {
        const updatedSelected = data.find((db: DatabaseInfo) => db.name === selectedDatabase.name);
        if (updatedSelected) {
          setSelectedDatabase(updatedSelected);
        } else if (data.length > 0) {
          setSelectedDatabase(data[0]);
        } else {
          setSelectedDatabase(null);
        }
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
