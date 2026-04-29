# Wiki sources

This folder is the source of truth for the [GitHub Wiki](https://github.com/knowledgestack/ks-mcp/wiki).
Editing here keeps wiki changes reviewable in PRs (the wiki's own git history is hard to audit).

## Sync to the live wiki

GitHub stores wiki pages in a sibling git repo: `<repo>.wiki.git`. To publish updates:

```bash
# one-time clone next to the main repo
git clone https://github.com/knowledgestack/ks-mcp.wiki.git ks-mcp.wiki

# whenever wiki/ changes here:
cp wiki/*.md ../ks-mcp.wiki/
cd ../ks-mcp.wiki
git add -A
git commit -m "docs(wiki): sync from main repo"
git push
```

A future GitHub Action under `.github/workflows/wiki-sync.yml` will automate this.

## Page index

| File | Live URL |
| --- | --- |
| `Home.md` | https://github.com/knowledgestack/ks-mcp/wiki |
| `Client-Setup.md` | https://github.com/knowledgestack/ks-mcp/wiki/Client-Setup |
| `Architecture.md` | https://github.com/knowledgestack/ks-mcp/wiki/Architecture |
| `Tools.md` | https://github.com/knowledgestack/ks-mcp/wiki/Tools |
| `Configuration.md` | https://github.com/knowledgestack/ks-mcp/wiki/Configuration |
| `Transports.md` | https://github.com/knowledgestack/ks-mcp/wiki/Transports |
| `Security.md` | https://github.com/knowledgestack/ks-mcp/wiki/Security |
| `Diagnostics.md` | https://github.com/knowledgestack/ks-mcp/wiki/Diagnostics |
| `Development.md` | https://github.com/knowledgestack/ks-mcp/wiki/Development |
| `Cookbook-Recipes.md` | https://github.com/knowledgestack/ks-mcp/wiki/Cookbook-Recipes |

GitHub-flavoured Markdown rendering on the wiki supports [Mermaid](https://github.blog/2022-02-14-include-diagrams-markdown-files-mermaid/) diagrams via fenced ` ```mermaid ` blocks — used throughout these pages.
