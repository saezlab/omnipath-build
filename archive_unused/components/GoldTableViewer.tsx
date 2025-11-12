'use client';

import { useState, useEffect } from 'react';
import Prism from 'prismjs';
import 'prismjs/components/prism-sql';
import 'prismjs/themes/prism-tomorrow.css';

interface ForeignKey {
  id: string;
  link: string;
}

interface GoldTable {
  name: string;
  sourceTable?: string;
  targetGoldTable?: string;
  sqlQuery?: string;
  foreignKeys: ForeignKey[];
  pass1Constraints: string[];
  pass2Constraints: string[];
}

export default function GoldTableViewer() {
  const [tables, setTables] = useState<GoldTable[]>([]);
  const [selectedTable, setSelectedTable] = useState<string>('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch('/api/config/gold-tables')
      .then(res => res.json())
      .then(data => {
        const parsedTables = parseGoldTables(data.rawContent);
        setTables(parsedTables);
        if (parsedTables.length > 0) {
          setSelectedTable(parsedTables[0].name);
        }
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    Prism.highlightAll();
  }, [selectedTable]);

  const parseGoldTables = (content: string): GoldTable[] => {
    const tables: GoldTable[] = [];

    // Parse silver_gold_map for SQL queries
    const mapMatch = content.match(/silver_gold_map\s*=\s*{([\s\S]*?)^}/m);
    if (!mapMatch) return tables;

    const mapContent = mapMatch[1];

    // Extract each mapping entry
    const entryRegex = /'([^']+)':\s*{([^}]+(?:{[^}]*}[^}]*)*)}/g;
    let match;
    const sqlMap: { [key: string]: { sourceTable?: string; targetGoldTable?: string; sql?: string } } = {};

    while ((match = entryRegex.exec(mapContent)) !== null) {
      const name = match[1];
      const body = match[2];

      const sourceTableMatch = body.match(/'source_table':\s*'([^']+)'/);
      const targetTableMatch = body.match(/'target_gold_table':\s*'([^']+)'/);
      const selectMatch = body.match(/'select':\s*'''([\s\S]*?)'''/);

      sqlMap[name] = {
        sourceTable: sourceTableMatch?.[1],
        targetGoldTable: targetTableMatch?.[1],
        sql: selectMatch?.[1]?.trim()
      };
    }

    // Parse gold_tables for structure info
    const goldTablesMatch = content.match(/gold_tables\s*=\s*{([\s\S]*?)^silver_gold_map/m);
    if (!goldTablesMatch) return tables;

    const goldContent = goldTablesMatch[1];
    const tableRegex = /"([^"]+)":\s*{([\s\S]*?)(?=\n    },?\n\n|\n}\n)/g;

    while ((match = tableRegex.exec(goldContent)) !== null) {
      const tableName = match[1];
      const tableBody = match[2];

      // Extract foreign keys
      const fkRegex = /fk\("([^"]+)",\s*"([^"]+)"\)/g;
      const foreignKeys: ForeignKey[] = [];
      let fkMatch;
      while ((fkMatch = fkRegex.exec(tableBody)) !== null) {
        foreignKeys.push({ id: fkMatch[1], link: fkMatch[2] });
      }

      // Extract constraints
      const pass1Match = tableBody.match(/"pass1":\s*\[(.*?)\]/s);
      const pass2Match = tableBody.match(/"pass2":\s*\[(.*?)\]/s);

      const pass1Constraints = pass1Match?.[1]
        ?.split(',')
        .map(c => c.trim().replace(/['"]/g, ''))
        .filter(c => c.length > 0) || [];

      const pass2Constraints = pass2Match?.[1]
        ?.split(',')
        .map(c => c.trim().replace(/['"]/g, ''))
        .filter(c => c.length > 0) || [];

      // Match with SQL query from silver_gold_map
      const sqlInfo = sqlMap[tableName] || {};

      tables.push({
        name: tableName,
        sourceTable: sqlInfo.sourceTable,
        targetGoldTable: sqlInfo.targetGoldTable,
        sqlQuery: sqlInfo.sql,
        foreignKeys,
        pass1Constraints,
        pass2Constraints
      });
    }

    // Add entries from sqlMap that don't have gold_tables definitions
    Object.entries(sqlMap).forEach(([name, info]) => {
      if (!tables.find(t => t.name === name)) {
        tables.push({
          name,
          sourceTable: info.sourceTable,
          targetGoldTable: info.targetGoldTable,
          sqlQuery: info.sql,
          foreignKeys: [],
          pass1Constraints: [],
          pass2Constraints: []
        });
      }
    });

    return tables.sort((a, b) => a.name.localeCompare(b.name));
  };

  if (loading) {
    return <div className="p-4">Loading...</div>;
  }

  const currentTable = tables.find(t => t.name === selectedTable);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <label className="font-medium">Table:</label>
        <select
          value={selectedTable}
          onChange={(e) => setSelectedTable(e.target.value)}
          className="px-3 py-1 border rounded bg-white"
        >
          {tables.map(table => (
            <option key={table.name} value={table.name}>{table.name}</option>
          ))}
        </select>
      </div>

      {currentTable && (
        <div className="border rounded p-4 space-y-4 bg-gray-50">
          {currentTable.sourceTable && (
            <div>
              <strong>Source Silver Table:</strong> <span className="font-mono text-sm">{currentTable.sourceTable}</span>
            </div>
          )}

          {currentTable.targetGoldTable && (
            <div>
              <strong>Target Gold Table:</strong> <span className="font-mono text-sm">{currentTable.targetGoldTable}</span>
            </div>
          )}

          {currentTable.sqlQuery && (
            <div>
              <h4 className="font-semibold mb-2">SQL Query:</h4>
              <pre className="rounded overflow-x-auto"><code className="language-sql">{currentTable.sqlQuery}</code></pre>
            </div>
          )}

          {currentTable.foreignKeys.length > 0 && (
            <div>
              <h4 className="font-semibold mb-2">Foreign Keys:</h4>
              <ul className="list-disc list-inside space-y-1 text-sm">
                {currentTable.foreignKeys.map((fk, idx) => (
                  <li key={idx}>
                    <span className="font-mono text-blue-600">{fk.id}</span>
                    {' → '}
                    <span className="text-gray-700">{fk.link}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(currentTable.pass1Constraints.length > 0 || currentTable.pass2Constraints.length > 0) && (
            <div>
              <h4 className="font-semibold mb-2">Constraints:</h4>
              <div className="space-y-2">
                {currentTable.pass1Constraints.length > 0 && (
                  <div>
                    <div className="text-sm font-medium text-gray-600">Pass 1:</div>
                    <ul className="list-disc list-inside text-sm">
                      {currentTable.pass1Constraints.map((constraint, idx) => (
                        <li key={idx} className="font-mono text-xs">{constraint}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {currentTable.pass2Constraints.length > 0 && (
                  <div>
                    <div className="text-sm font-medium text-gray-600">Pass 2:</div>
                    <ul className="list-disc list-inside text-sm">
                      {currentTable.pass2Constraints.map((constraint, idx) => (
                        <li key={idx} className="font-mono text-xs">{constraint}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
