<!-- Thanks for contributing! Delete sections that don't apply. -->

## Summary

<!-- 1–3 sentences. What changed and why? -->

## Type of change

- [ ] 🐛 Bug fix
- [ ] ✨ New tool
- [ ] 🔌 Transport / auth change
- [ ] 📖 Documentation
- [ ] 🧰 Tooling / CI
- [ ] 🧹 Refactor
- [ ] 💥 Breaking change (describe migration in "Notes")

## Related issues

<!-- "Closes #123" / "Fixes #456" -->

## Test plan

- [ ] `make lint` / `uv run ruff check .`
- [ ] `uv run --extra dev pytest tests/ -v` green
- [ ] Manually verified with the MCP Inspector (`npx @modelcontextprotocol/inspector uvx knowledgestack-mcp`) if the change touches tool behavior
- [ ] No secrets, PII, or tenant data in logs

## Checklist

- [ ] No API keys in the diff
- [ ] Tool descriptions are clear and complete
- [ ] Read-only contract preserved (no write operations introduced without explicit discussion)
- [ ] Updated `mcp-python` README if the tool surface changed

## Notes
