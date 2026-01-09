// Shared entity type display utilities

// Map entity types to emojis for visual distinction
export const entityTypeEmojis: Record<string, string> = {
    'SmallMolecule': '🧪',
    'Lipid': '💧',
    'Cv_term': '🏷️',
    'Protein': '🧬',
    'Reaction': '⚗️',
    'Complex': '🧩',
    'Pathway': '🛣️',
    'Protein_family': '👥',
    'Physical_entity': '🧱',
    'DoubleStrandedDeoxyribonucleicAcid': '🧬',
    'ProteinComplex': '🧩',
    'RibonucleicAcid': '🧬',
    'Phenotype': '🩺',
    'MoleculeSet': '📦',
    'Stimulus': '🔦',
    'Degradation': '♻️',
};

// Normalized keys for case-insensitive matching
// Maps lowercase, no-space versions to the original keys
const normalizedEntityTypeMap: Record<string, string> = Object.keys(entityTypeEmojis).reduce((acc, key) => {
    // Create normalized key: lowercase, no spaces, no underscores
    const normalized = key.toLowerCase().replace(/[_\s]/g, '');
    acc[normalized] = key;
    return acc;
}, {} as Record<string, string>);

/**
 * Get emoji for an entity type value.
 * Handles case-insensitive matching and various naming formats:
 * - "TypeName:ID" format (e.g., "Protein:12345")
 * - lowercase (e.g., "protein")
 * - space-separated (e.g., "small molecule")
 * - underscore-separated (e.g., "PHYSICAL_ENTITY")
 */
export function getEntityTypeEmoji(value: string): string | undefined {
    // Extract type name from "TypeName:ID" format if present
    const typeName = value.includes(':') ? value.split(':')[0] : value;

    // Try direct match first
    if (entityTypeEmojis[typeName]) {
        return entityTypeEmojis[typeName];
    }

    // Try normalized (case-insensitive, no spaces/underscores) match
    const normalized = typeName.toLowerCase().replace(/[_\s]/g, '');
    const originalKey = normalizedEntityTypeMap[normalized];
    if (originalKey) {
        return entityTypeEmojis[originalKey];
    }

    return undefined;
}
