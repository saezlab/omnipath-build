import { skills } from '$lib/skills';
import { renderSkill } from '$lib/skills/markdown';

export const load = () => ({
  skills: skills.map((skill) => ({ ...skill, html: renderSkill(skill.content) })),
});
