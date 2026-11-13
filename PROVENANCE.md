# Codex Council Provenance

- Method inspiration: https://github.com/karpathy/llm-council and https://llm-council.dev/.
- Implementation: local clean-room Codex workflow and stdlib Python utility.
- External model APIs: none; the workflow uses Codex agents only.
- Logo: provided by the plugin maintainer on 2026-05-20 and redrawn as a five-square vector mark for 1.2.0.
- Interview inspiration: the Grill Me workflow in engineering-suite-grill-me uses decision dependencies and questions grounded in the repository. The local brief format, frontier utility and architecture workflow are original to this plugin.
- Adaptation note: the original pattern uses multiple LLM providers, anonymized peer review, ranking, and Chairman synthesis. Codex Council preserves that workflow shape without reusing upstream code or requiring external model providers.

- Fork additions: input identity, evidence and Truey profile by Hassanjk99.
  Snapshot/freshness and observation-versus-claim separation were informed by
  https://github.com/ehzawad/codex-council at afb2221. The implementation here is
  original; its Claude orchestration and CLI supervisor were not copied.
