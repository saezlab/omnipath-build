import contributeContent from '../../../../../.cursor/skills/omnipath-contribute/SKILL.md?raw';
import parquetContent from '../../../../../.cursor/skills/omnipath-parquet/SKILL.md?raw';

export type Skill = {
  name: string;
  title: string;
  description: string;
  capabilities: string[];
  href: string;
  content: string;
};

export const skills: Skill[] = [
  {
    name: 'omnipath-parquet',
    title: 'Use OmniPath',
    description: 'Find resources, download data, and understand entities and relations.',
    capabilities: [
      'Browse versioned resources',
      'Download Parquet files',
      'Work with entities, relations, and evidence',
    ],
    href: '/skills/omnipath-parquet/SKILL.md',
    content: parquetContent,
  },
  {
    name: 'omnipath-contribute',
    title: 'Contribute to OmniPath',
    description: 'Teach your agent to add a data resource and submit it to pypath.',
    capabilities: [
      'Learn from the input documentation and examples',
      'Implement and test a new resource module',
      'Open a pull request in saezlab/pypath',
    ],
    href: '/skills/omnipath-contribute/SKILL.md',
    content: contributeContent,
  },
];

export function getSkill(name: string) {
  return skills.find((skill) => skill.name === name);
}
