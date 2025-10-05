import fs from 'fs';
import path from 'path';
import { TreeNode } from '../components/DatabaseTree';

export interface DatabaseFile {
  path: string;
  name: string;
  layer: 'bronze' | 'silver' | 'gold';
  database: string;
  size: number;
  modified: Date;
}

export interface DatabaseInfo {
  name: string;
  path: string;
  layers: {
    bronze: DatabaseFile[];
    silver: DatabaseFile[];
    gold: DatabaseFile[];
  };
  totalFiles: number;
  totalSize: number;
}

const DATABASES_PATH = path.join(process.cwd(), '..', 'databases');

function getLayerFromPath(filePath: string): 'bronze' | 'silver' | 'gold' | null {
  if (filePath.includes('/bronze/')) return 'bronze';
  if (filePath.includes('/silver_parquet/')) return 'silver';
  if (filePath.includes('/gold_parquet/')) return 'gold';
  return null;
}

function scanDirectory(dirPath: string, baseDir: string = ''): DatabaseFile[] {
  const files: DatabaseFile[] = [];
  
  try {
    const items = fs.readdirSync(dirPath);
    
    for (const item of items) {
      const fullPath = path.join(dirPath, item);
      const stat = fs.statSync(fullPath);
      
      if (stat.isDirectory()) {
        files.push(...scanDirectory(fullPath, baseDir));
      } else if (item.endsWith('.parquet')) {
        const layer = getLayerFromPath(fullPath);
        if (layer) {
          files.push({
            path: fullPath.replace(DATABASES_PATH, ''),
            name: item,
            layer,
            database: baseDir,
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
  const databases: DatabaseInfo[] = [];
  
  try {
    const items = fs.readdirSync(DATABASES_PATH);
    
    for (const item of items) {
      const dbPath = path.join(DATABASES_PATH, item);
      const stat = fs.statSync(dbPath);
      
      if (stat.isDirectory()) {
        const files = scanDirectory(dbPath, item);
        
        const dbInfo: DatabaseInfo = {
          name: item,
          path: dbPath,
          layers: {
            bronze: files.filter(f => f.layer === 'bronze'),
            silver: files.filter(f => f.layer === 'silver'),
            gold: files.filter(f => f.layer === 'gold')
          },
          totalFiles: files.length,
          totalSize: files.reduce((sum, f) => sum + f.size, 0)
        };
        
        databases.push(dbInfo);
      }
    }
  } catch (error) {
    console.error('Error scanning databases:', error);
  }
  
  return databases;
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
    children: [
      {
        name: 'Bronze Layer',
        path: `${db.path}/bronze`,
        type: 'layer' as const,
        layer: 'bronze' as const,
        children: buildDirectoryStructure(db.layers.bronze, `/bronze`, db.name)
      },
      {
        name: 'Silver Layer',
        path: `${db.path}/silver_parquet`,
        type: 'layer' as const,
        layer: 'silver' as const,
        children: buildDirectoryStructure(db.layers.silver, `/silver_parquet`, db.name)
      },
      {
        name: 'Gold Layer',
        path: `${db.path}/gold_parquet`,
        type: 'layer' as const,
        layer: 'gold' as const,
        children: buildDirectoryStructure(db.layers.gold, `/gold_parquet`, db.name)
      }
    ].filter(layer => layer.children && layer.children.length > 0)
  }));
}

export async function loadParquetFile(filePath: string): Promise<ArrayBuffer> {
  const fullPath = path.join(DATABASES_PATH, filePath);
  const buffer = await fs.promises.readFile(fullPath);
  // Convert Buffer to ArrayBuffer properly
  const arrayBuffer = new ArrayBuffer(buffer.length);
  const view = new Uint8Array(arrayBuffer);
  view.set(buffer);
  return arrayBuffer;
}
