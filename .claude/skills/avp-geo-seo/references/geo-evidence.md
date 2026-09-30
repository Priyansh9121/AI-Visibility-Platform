# GEO evidence (`docs/north-star.md` §9.1, dated 2026-09-09)

Close to the whole primary literature as of that date. Informational, not scoped. Re-check for newer work before relying on it.

| Source | Supports | Does not support |
| --- | --- | --- |
| Aggarwal et al., "GEO: Generative Engine Optimization" (arXiv:2311.09735, KDD 2024) | GEO techniques boosted visibility "up to 40% in its benchmark" (GEO-bench), effectiveness varying by domain. | A general uplift. The figure is a best case on one benchmark. |
| Martinez, "Optimizing Visibility in Generative Engines: A Critical Survey (2023-2026)" (arXiv:2607.14035), 45 studies. Read first. | The 40% gain is conditional on a source already present in a fixed context and does not establish organic discoverability. Topical relevance and context position are the most reproducible levers. Citation-oriented rewrites can impair retrieval. Commercial audits show low cross-engine source overlap and substantial run-to-run variability. | Any claim that a given change will get a brand cited. |
| Kumar & Lakkaraju, "Manipulating Large Language Models to Increase Product Visibility" (arXiv:2404.07981) | A "strategic text sequence" in a product page can raise its odds of being the top recommendation. | Use as a technique. Defensive only: never hidden text, strategic sequences or prompt injection. |
| "EcoGEO: Trajectory-Aware Evidence Ecosystems for Web-Enabled LLM Search Agents" (arXiv:2605.12887) | A consideration-set lens (how an LLM narrows to the few names it gives) on Share of Voice. | Any specific tactic. |

Not sourced anywhere in this skill, so not to be stated as fact: token-length rules for extractable sentences, freshness percentages, FAQ word counts, ranking-tier effects, llms.txt effectiveness.

Crawler tokens are a separate, vendor-documented class of fact; see SKILL.md, fetched 2026-09-30, re-verify before shipping.
