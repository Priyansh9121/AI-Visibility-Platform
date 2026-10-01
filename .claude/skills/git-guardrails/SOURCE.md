# Source

- Repo folder: `~/Skills-archive/All-Skills/skills-main/skills/misc/git-guardrails-claude-code/`
- Files read: `SKILL.md`, `scripts/block-dangerous-git.sh`, `agents/openai.yaml`
- Upstream URL: not stated in the source folder or its LICENSE
- Licence: MIT, "Copyright (c) 2026 Matt Pocock" (`skills-main/LICENSE`, copied byte-for-byte to `LICENSE`)
- Date copied: 2026-09-30
- Rule syntax source: https://code.claude.com/docs/en/permissions (excerpt fetched 2026-09-30)

| File | sha256 |
| :- | :- |
| SKILL.md | 69b291d655626a01602b998a1836fcb93c058ede68dfafddfd71e87f17074764 |
| scripts/block-dangerous-git.sh | 234922b83c0a1737ee7300806c21ac0f389b07aaeb65c2d71ccedafbc5e1ea4b |
| agents/openai.yaml | d53cd783fc0ac208dec9a4c267989284384785aab6422dccdaed42f9e40243ec |
| ../../LICENSE | 0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5 |

`scripts/block-dangerous-git.sh` was deliberately NOT copied (the founder's rule
for this library is no hooks), and its contents were not executed. `agents/openai.yaml`
was not copied (folder contract). Only the idea and the list of guarded commands
carry over; the mechanism is rebuilt with native permission rules.
