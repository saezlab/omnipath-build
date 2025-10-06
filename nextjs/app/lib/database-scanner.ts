import fs from 'fs';
import path from 'path';
import { TreeNode } from '../components/DatabaseTree';

export interface DatabaseFile {
  path: string;
  name: string;
  layer: 'bronze' | 'silver' | 'gold' | 'pass1';
  source: string;
  size: number;
  modified: Date;
}

export interface SourceInfo {
  name: string;
  path: string;
  layers: {
    bronze: DatabaseFile[];
    silver: DatabaseFile[];
    gold: DatabaseFile[];
    pass1: DatabaseFile[];
  };
  totalFiles: number;
  totalSize: number;
}

export interface DatabaseInfo {
  name: string;
  path: string;
  sources: SourceInfo[];
  totalFiles: number;
  totalSize: number;
}

const DATABASES_PATH = path.join(process.cwd(), '..', 'databases', 'omnipath', 'data');

function getLayerFromPath(filePath: string): 'bronze' | 'silver' | 'gold' | 'pass1' | null {
  if (filePath.includes('/bronze/')) return 'bronze';
  if (filePath.includes('/silver/')) return 'silver';
  if (filePath.includes('/gold/')) return 'gold';
  if (filePath.includes('/pass1/')) return 'pass1';
  return null;
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

export function scanDatabases(): DatabaseInfo[] {
  const sources: SourceInfo[] = [];

  try {
    const sourceTypes = fs.readdirSync(DATABASES_PATH);

    for (const sourceType of sourceTypes) {
      const sourceTypePath = path.join(DATABASES_PATH, sourceType);
      const stat = fs.statSync(sourceTypePath);

      if (stat.isDirectory()) {
        // Each source type directory contains subdirectories (e.g., lipidmaps/lipidmaps_lipids)
        const sourceSubdirs = fs.readdirSync(sourceTypePath);

        for (const subdir of sourceSubdirs) {
          const subdirPath = path.join(sourceTypePath, subdir);
          const subdirStat = fs.statSync(subdirPath);

          if (subdirStat.isDirectory()) {
            const files = scanDirectory(subdirPath, subdir);

            const sourceInfo: SourceInfo = {
              name: subdir,
              path: subdirPath,
              layers: {
                bronze: files.filter(f => f.layer === 'bronze'),
                silver: files.filter(f => f.layer === 'silver'),
                gold: files.filter(f => f.layer === 'gold'),
                pass1: files.filter(f => f.layer === 'pass1')
              },
              totalFiles: files.length,
              totalSize: files.reduce((sum, f) => sum + f.size, 0)
            };

            sources.push(sourceInfo);
          }
        }
      }
    }
  } catch (error) {
    console.error('Error scanning databases:', error);
  }

  // Return omnipath as a single database with all sources
  const totalFiles = sources.reduce((sum, s) => sum + s.totalFiles, 0);
  const totalSize = sources.reduce((sum, s) => sum + s.totalSize, 0);

  return [{
    name: 'omnipath',
    path: DATABASES_PATH,
    sources,
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
      children: [
        {
          name: 'Bronze Layer',
          path: `${source.path}/bronze`,
          type: 'layer' as const,
          layer: 'bronze' as const,
          children: buildDirectoryStructure(source.layers.bronze, `/bronze`, source.name)
        },
        {
          name: 'Silver Layer',
          path: `${source.path}/silver`,
          type: 'layer' as const,
          layer: 'silver' as const,
          children: buildDirectoryStructure(source.layers.silver, `/silver`, source.name)
        },
        {
          name: 'Gold Layer',
          path: `${source.path}/gold`,
          type: 'layer' as const,
          layer: 'gold' as const,
          children: buildDirectoryStructure(source.layers.gold, `/gold`, source.name)
        },
        {
          name: 'Pass1 Layer',
          path: `${source.path}/pass1`,
          type: 'layer' as const,
          layer: 'pass1' as const,
          children: buildDirectoryStructure(source.layers.pass1, `/pass1`, source.name)
        }
      ].filter(layer => layer.children && layer.children.length > 0)
    }))
  }));
}

export async function loadParquetFile(filePath: string): Promise<ArrayBuffer> {
  // If filePath already includes databases/omnipath, use it directly
  const fullPath = filePath.startsWith('/databases/omnipath')
    ? path.join(process.cwd(), '..', filePath)
    : path.join(DATABASES_PATH, filePath);
  const buffer = await fs.promises.readFile(fullPath);
  // Convert Buffer to ArrayBuffer properly
  const arrayBuffer = new ArrayBuffer(buffer.length);
  const view = new Uint8Array(arrayBuffer);
  view.set(buffer);
  return arrayBuffer;
}
