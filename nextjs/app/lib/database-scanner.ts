import fs from 'fs';
import path from 'path';
import { TreeNode } from '../components/DatabaseTree';

export interface DatabaseFile {
  path: string;
  name: string;
  layer: 'silver';
  source: string;
  size: number;
  modified: Date;
}

export interface SourceInfo {
  name: string;
  path: string;
  layers: {
    silver: DatabaseFile[];
  };
  totalFiles: number;
  totalSize: number;
}

export interface CombinedTable {
  name: string;
  path: string;
  size: number;
  modified: Date;
}

export interface DatabaseInfo {
  name: string;
  path: string;
  sources: SourceInfo[];
  combinedTables: CombinedTable[];
  totalFiles: number;
  totalSize: number;
}

const DATABASES_PATH = path.join(process.cwd(), '..', 'databases', 'omnipath', 'data');
const GOLD_PATH = path.join(process.cwd(), '..', 'omnipath_build', 'gold_duckdb');

function getLayerFromPath(_filePath: string): 'silver' | null {
  // All parquet files in source directories are silver files
  // Pattern: /source_name/file.parquet
  return 'silver';
}

function scanDirectory(dirPath: string, sourceName: string = ''): DatabaseFile[] {
  const files: DatabaseFile[] = [];

  try {
    const items = fs.readdirSync(dirPath);

    for (const item of items) {
      const fullPath = path.join(dirPath, item);
      const stat = fs.statSync(fullPath);

      if (stat.isDirectory()) {
        files.push(...scanDirectory(fullPath, sourceName));
      } else if (item.endsWith('.parquet')) {
        const layer = getLayerFromPath(fullPath);
        if (layer) {
          files.push({
            path: fullPath.replace(DATABASES_PATH, ''),
            name: item,
            layer,
            source: sourceName,
            size: stat.size,
            modified: stat.mtime
          });
        }
      }
    }
  } catch (error) {
    console.error(`Error scanning directory ${dirPath}:`, error);
  }

  return files;
}

function scanCombinedTables(): CombinedTable[] {
  const tables: CombinedTable[] = [];

  try {
    if (!fs.existsSync(GOLD_PATH)) {
      return tables;
    }

    const items = fs.readdirSync(GOLD_PATH);

    for (const item of items) {
      if (item.endsWith('.parquet')) {
        const fullPath = path.join(GOLD_PATH, item);
        const stat = fs.statSync(fullPath);

        tables.push({
          name: item.replace('.parquet', ''),
          path: fullPath,
          size: stat.size,
          modified: stat.mtime
        });
      }
    }
  } catch (error) {
    console.error('Error scanning gold tables:', error);
  }

  return tables;
}

export function scanDatabases(): DatabaseInfo[] {
  const sources: SourceInfo[] = [];

  try {
    const sourceNames = fs.readdirSync(DATABASES_PATH);

    for (const sourceName of sourceNames) {
      const sourcePath = path.join(DATABASES_PATH, sourceName);
      const stat = fs.statSync(sourcePath);

      if (stat.isDirectory()) {
        // Scan for parquet files directly in the source directory
        const files = scanDirectory(sourcePath, sourceName);

        const sourceInfo: SourceInfo = {
          name: sourceName,
          path: sourcePath,
          layers: {
            silver: files.filter(f => f.layer === 'silver')
          },
          totalFiles: files.length,
          totalSize: files.reduce((sum, f) => sum + f.size, 0)
        };

        // Only add sources that have files
        if (files.length > 0) {
          sources.push(sourceInfo);
        }
      }
    }
  } catch (error) {
    console.error('Error scanning databases:', error);
  }

  // Scan combined output tables
  const combinedTables = scanCombinedTables();

  // Return omnipath as a single database with all sources
  const totalFiles = sources.reduce((sum, s) => sum + s.totalFiles, 0);
  const totalSize = sources.reduce((sum, s) => sum + s.totalSize, 0);

  return [{
    name: 'omnipath',
    path: DATABASES_PATH,
    sources,
    combinedTables,
    totalFiles,
    totalSize
  }];
}

export function getSource(sourceName: string): SourceInfo | null {
  const databases = scanDatabases();
  if (databases.length === 0) return null;

  const omnipath = databases[0];
  return omnipath.sources.find(s => s.name === sourceName) || null;
}

interface DirStructure {
  _type: 'folder' | 'file';
  children?: { [key: string]: DirStructure };
  file?: DatabaseFile;
}

function buildDirectoryStructure(files: DatabaseFile[], layerPath: string, dbName: string): TreeNode[] {
  const structure: { [key: string]: DirStructure } = {};

  files.forEach(file => {
    const relativePath = file.path.replace(layerPath, '').replace(/^\//, '');
    let pathParts = relativePath.split('/').filter(part => part !== '');

    // Remove common redundant folders
    pathParts = pathParts.filter(part => part !== 'data' && part !== dbName);

    let current: { [key: string]: DirStructure } = structure;

    // Build nested structure
    for (let i = 0; i < pathParts.length - 1; i++) {
      const part = pathParts[i];
      if (!current[part]) {
        current[part] = { _type: 'folder', children: {} };
      }
      current = current[part].children!;
    }

    // Add the file
    const fileName = pathParts[pathParts.length - 1];
    current[fileName] = {
      _type: 'file',
      file: file
    };
  });

  // Convert structure to TreeNode array
  function convertToTreeNodes(obj: { [key: string]: DirStructure }, basePath: string = ''): TreeNode[] {
    return Object.entries(obj).map(([name, data]) => {
      const fullPath = basePath ? `${basePath}/${name}` : name;

      if (data._type === 'file') {
        return {
          name: data.file!.name,
          path: data.file!.path,
          type: 'file' as const
        };
      } else {
        return {
          name: name,
          path: fullPath,
          type: 'folder' as const,
          children: convertToTreeNodes(data.children!, fullPath)
        };
      }
    });
  }

  return convertToTreeNodes(structure);
}

export function buildDatabaseTree(databases: DatabaseInfo[]): TreeNode[] {
  return databases.map(db => ({
    name: db.name,
    path: db.path,
    type: 'database' as const,
    children: db.sources.map(source => ({
      name: source.name,
      path: source.path,
      type: 'folder' as const,
      // Files are directly in the source folder, no layer subdirectories
      children: buildDirectoryStructure(source.layers.silver, '', source.name)
    }))
  }));
}

export async function loadParquetFile(filePath: string): Promise<ArrayBuffer> {
  let fullPath: string;

  // Check if it's an absolute path pointing to the gold directory (combined tables)
  if (path.isAbsolute(filePath) && filePath.includes('/gold_duckdb/')) {
    fullPath = filePath;
  }
  // If filePath starts with / but is relative to DATABASES_PATH (source-specific files)
  else if (filePath.startsWith('/') && !filePath.includes('/gold_duckdb/')) {
    fullPath = path.join(DATABASES_PATH, filePath);
  }
  // If filePath already includes databases/omnipath, use it directly
  else if (filePath.includes('databases/omnipath')) {
    fullPath = path.join(process.cwd(), '..', filePath);
  }
  // Otherwise, relative to DATABASES_PATH
  else {
    fullPath = path.join(DATABASES_PATH, filePath);
  }

  const buffer = await fs.promises.readFile(fullPath);
  // Convert Buffer to ArrayBuffer properly
  const arrayBuffer = new ArrayBuffer(buffer.length);
  const view = new Uint8Array(arrayBuffer);
  view.set(buffer);
  return arrayBuffer;
}
