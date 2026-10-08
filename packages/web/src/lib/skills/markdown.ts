import { Marked } from 'marked';

// SKILL.md files are first-party repository content, so their HTML is trusted.
const markdown = new Marked({
  gfm: true,
  renderer: {
    link({ href, title, tokens }) {
      const text = this.parser.parseInline(tokens);
      const external = /^https?:\/\//.test(href);
      return `<a href="${href}"${title ? ` title="${title}"` : ''}${
        external ? ' target="_blank" rel="noreferrer"' : ''
      }>${text}</a>`;
    },
  },
});

/** A SKILL.md without its YAML front matter (name and description are shown separately). */
export function skillBody(content: string): string {
  return content.replace(/^---\n[\s\S]*?\n---\n?/, '').trim();
}

export function renderSkill(content: string): string {
  return markdown.parse(skillBody(content), { async: false });
}
