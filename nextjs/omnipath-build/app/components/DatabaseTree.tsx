'use client';

import React, { useState } from 'react';
import { ChevronRight, ChevronDown, Database, Folder, File } from 'lucide-react';
import LayerBadge from './LayerBadge';

export interface TreeNode {
  name: string;
  path: string;
  type: 'database' | 'layer' | 'folder' | 'file';
  layer?: 'bronze' | 'silver' | 'gold';
  children?: TreeNode[];
}

interface DatabaseTreeProps {
  data: TreeNode[];
  onFileSelect?: (path: string) => void;
}

function TreeItem({ node, onFileSelect, depth = 0 }: { node: TreeNode; onFileSelect?: (path: string) => void; depth?: number }) {
  const [isExpanded, setIsExpanded] = useState(depth < 2);

  const handleClick = () => {
    if (node.type === 'file') {
      onFileSelect?.(node.path);
    } else if (node.children) {
      setIsExpanded(!isExpanded);
    }
  };

  const getIcon = () => {
    if (node.type === 'database') return <Database className="w-4 h-4" />;
    if (node.type === 'folder' || node.type === 'layer') return <Folder className="w-4 h-4" />;
    return <File className="w-4 h-4" />;
  };

  return (
    <div>
      <div
        className={`flex items-center gap-2 px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md cursor-pointer transition-colors ${
          node.type === 'file' ? 'text-blue-600 dark:text-blue-400' : ''
        }`}
        style={{ paddingLeft: `${depth * 1.5}rem` }}
        onClick={handleClick}
      >
        {node.children && (
          <span className="text-gray-400">
            {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </span>
        )}
        {!node.children && <span className="w-4" />}
        {getIcon()}
        <span className="flex-1 text-sm font-medium">{node.name}</span>
        {node.layer && <LayerBadge layer={node.layer} className="ml-2 scale-75" />}
      </div>
      {isExpanded && node.children && (
        <div>
          {node.children.map((child) => (
            <TreeItem key={child.path} node={child} onFileSelect={onFileSelect} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function DatabaseTree({ data, onFileSelect }: DatabaseTreeProps) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-4">Database Structure</h3>
      <div className="space-y-1">
        {data.map((node) => (
          <TreeItem key={node.path} node={node} onFileSelect={onFileSelect} />
        ))}
      </div>
    </div>
  );
}
