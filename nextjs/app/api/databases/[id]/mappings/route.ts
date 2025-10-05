import { NextRequest, NextResponse } from 'next/server';
import * as fs from 'fs/promises';
import * as path from 'path';
import * as yaml from 'js-yaml';

interface FieldMapping {
  source: string;
  target: string;
  transform?: string;
  description?: string;
}

interface ResourceConfig {
  metadata: {
    name: string;
    description: string;
  };
  module: string;
  functions: {
    [key: string]: {
      processing: {
        target_table: string;
        field_mapping: FieldMapping[];
      };
    };
  };
}

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const projectRoot = path.join(process.cwd(), '..');
    const resourcePath = path.join(
      projectRoot,
      'databases',
      id,
      'resource'
    );

    // Check if resource directory exists
    try {
      await fs.access(resourcePath);
    } catch {
      return NextResponse.json(
        { error: 'Database resource configurations not found' },
        { status: 404 }
      );
    }

    // Read all YAML files in the resource directory
    const files = await fs.readdir(resourcePath);
    const yamlFiles = files.filter(file => file.endsWith('.yaml') || file.endsWith('.yml'));

    const mappings = [];
    
    for (const file of yamlFiles) {
      const filePath = path.join(resourcePath, file);
      const content = await fs.readFile(filePath, 'utf8');
      
      try {
        const config = yaml.load(content) as ResourceConfig;
        
        if (config && config.functions) {
          for (const [functionName, functionConfig] of Object.entries(config.functions)) {
            if (functionConfig.processing && functionConfig.processing.field_mapping) {
              mappings.push({
                resource: config.metadata?.name || config.module || file.replace('.yaml', ''),
                module: config.module,
                function: functionName,
                targetTable: functionConfig.processing.target_table,
                fieldMappings: functionConfig.processing.field_mapping,
                description: config.metadata?.description || ''
              });
            }
          }
        }
      } catch (error) {
        console.error(`Error parsing YAML file ${file}:`, error);
      }
    }

    // Also check for bronze and silver parquet files to get actual table structures
    const bronzePath = path.join(
      projectRoot,
      'databases',
      id,
      'bronze',
      'data'
    );

    const silverPath = path.join(
      projectRoot,
      'databases',
      id,
      'silver_parquet'
    );

    const bronzeTables = [];
    const silverTables = [];

    // Get bronze tables
    try {
      const bronzeExists = await fs.access(bronzePath).then(() => true).catch(() => false);
      if (bronzeExists) {
        const bronzeDirs = await fs.readdir(bronzePath);
        for (const dir of bronzeDirs) {
          const dirPath = path.join(bronzePath, dir);
          const stat = await fs.stat(dirPath);
          if (stat.isDirectory()) {
            const subDirs = await fs.readdir(dirPath);
            for (const subDir of subDirs) {
              const subDirPath = path.join(dirPath, subDir);
              const subStat = await fs.stat(subDirPath);
              if (subStat.isDirectory()) {
                bronzeTables.push({
                  resource: dir,
                  table: subDir
                });
              }
            }
          }
        }
      }
    } catch (error) {
      console.error('Error reading bronze tables:', error);
    }

    // Get silver tables (now parquet files directly in silver_parquet/)
    try {
      const silverExists = await fs.access(silverPath).then(() => true).catch(() => false);
      if (silverExists) {
        const silverFiles = await fs.readdir(silverPath);
        for (const file of silverFiles) {
          if (file.endsWith('.parquet')) {
            // Extract table name from filename (e.g., hmdb_compounds_for_metabo_silver_entities.parquet)
            const tableName = file.replace('.parquet', '');
            silverTables.push(tableName);
          }
        }
      }
    } catch (error) {
      console.error('Error reading silver tables:', error);
    }
    
    // Load silver table definitions from YAML if available
    let silverTableDefinitions: Record<string, unknown> = {};
    const silverTablesYamlPath = path.join(
      projectRoot,
      'databases',
      id,
      'silver',
      'tables.yaml'
    );
    
    try {
      const silverTablesYamlExists = await fs.access(silverTablesYamlPath).then(() => true).catch(() => false);
      if (silverTablesYamlExists) {
        const content = await fs.readFile(silverTablesYamlPath, 'utf8');
        silverTableDefinitions = yaml.load(content) as Record<string, unknown>;
      }
    } catch (error) {
      console.error('Error reading silver table definitions:', error);
    }

    return NextResponse.json({
      database: id,
      mappings,
      bronzeTables,
      silverTables,
      silverTableDefinitions
    });

  } catch (error) {
    console.error('Error fetching mappings:', error);
    return NextResponse.json(
      { error: 'Failed to fetch mapping data' },
      { status: 500 }
    );
  }
}
