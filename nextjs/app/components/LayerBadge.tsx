import React from 'react';

interface LayerBadgeProps {
  layer: 'bronze' | 'silver' | 'gold';
  className?: string;
}

export default function LayerBadge({ layer, className = '' }: LayerBadgeProps) {
  const layerStyles = {
    bronze: 'bg-gradient-to-r from-orange-600 to-amber-600 text-white',
    silver: 'bg-gradient-to-r from-gray-400 to-slate-500 text-white',
    gold: 'bg-gradient-to-r from-yellow-500 to-amber-500 text-white'
  };

  return (
    <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold uppercase tracking-wider ${layerStyles[layer]} ${className}`}>
      {layer}
    </span>
  );
}
