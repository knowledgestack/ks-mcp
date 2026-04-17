# Security Policy

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report privately via one of:

1. **GitHub Private Vulnerability Reporting** (preferred) — [report here](https://github.com/knowledgestack/ks-mcp/security/advisories/new).
2. **Email** — `security@knowledgestack.ai` (include "ks-mcp" in the subject).

Include where possible:

- Affected version (`knowledgestack-mcp` PyPI version or commit SHA)
- Reproduction steps
- Impact (credential exposure, code execution, privilege escalation, DoS)
- Proof-of-concept if you have one

## Scope

In scope:
- The `knowledgestack-mcp` Python package and the published PyPI artifact
- Supply-chain issues in our dependencies with a clear exploit path
- Misconfigurations in our CI/CD that could allow unauthorized pushes

Out of scope:
- Vulnerabilities in the hosted Knowledge Stack API — report those at `security@knowledgestack.ai` directly
- Issues in forked or modified copies of this server
- Prompt-injection in agents built on top of this server (that's the consumer's responsibility)

## Response

- Acknowledgement within **2 business days**
- Triage within **5 business days**
- Critical fixes target **7 days**; lower severity within the next release cycle
- Default disclosure window: **90 days** from report, adjusted by mutual agreement

## Safe harbor

Good-faith security research is welcomed. We won't pursue legal action against researchers who avoid data destruction / privacy violations, only test accounts they own, give us time to fix before disclosing, and don't monetize pre-disclosure.
