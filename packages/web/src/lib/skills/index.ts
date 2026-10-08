import contributeContent from '../../../content/skills/omnipath-contribute/SKILL.md?raw';
import parquetContent from '../../../content/skills/omnipath-parquet/SKILL.md?raw';

export type Skill = {
  name: string;
  title: string;
  /** What the skill is for, shown as a tag: using the data or contributing to it. */
  kind: 'Use the data' | 'Contribute';
  description: string;
  capabilities: string[];
  /** Requests an agent with the skill can act on, shown as things to try. */
  examples: string[];
  href: string;
  content: string;
};

export const skills: Skill[] = [
  {
    name: 'omnipath-parquet',
    title: 'Use OmniPath',
    description: 'Find resources, download data, and understand entities and relations.',
    kind: 'Use the data',
    capabilities: [
      'Browse versioned resources',
      'Download Parquet files',
      'Work with entities, relations, and evidence',
    ],
    examples: [
      'Download the SIGNOR relations and count them by predicate.',
      'Which resources describe ligand–receptor interactions, and how many relations does each have?',
      'Find every relation that involves TP53 in the latest release and summarise its partners.',
    ],
    href: '/skills/omnipath-parquet/SKILL.md',
    content: parquetContent,
  },
  {
    name: 'omnipath-contribute',
    title: 'Contribute to OmniPath',
    description: 'Teach your agent to add a data resource and submit it to pypath.',
    kind: 'Contribute',
    capabilities: [
      'Learn from the input documentation and examples',
      'Implement and test a new resource module',
      'Open a pull request in saezlab/pypath',
    ],
    examples: [
      'Add the Therapeutic Target Database (TTD) drug–target table as a new pypath resource.',
      'Write an inputs_v2 module for this TSV of drug–target interactions and test it.',
      'Open a pull request to saezlab/pypath with the new resource and its tests.',
    ],
    href: '/skills/omnipath-contribute/SKILL.md',
    content: contributeContent,
  },
];

export function getSkill(name: string) {
  return skills.find((skill) => skill.name === name);
}
