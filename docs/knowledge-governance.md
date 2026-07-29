# Knowledge governance

The public Foundation v10 corpus contains focused methods and contracts, not
complete tutorial solutions. Every YAML entry carries an ID, applicability
boundary, source hash and license, leakage metadata, and validation guidance.
`src/foampilot/knowledge/knowledge-manifest.json` freezes the entry
bytes.

Retrieval applies fork, version, solver, knowledge type, visibility, and family
filters before relevance scoring. An entry is unavailable whenever its
`leakage.families` contains the active evaluation family.

`development_only` knowledge is excluded from formal retrieval unless the
protocol explicitly allowlists the active family for development. Any
experiment-derived entries must be `development_only` and list their leakage
families. This is a qualification mechanism, not permission to expose an
evaluation target to another family.

Promotion from development evidence to public knowledge requires:

1. extracting a solver-independent or broadly applicable lesson;
2. replacing target-specific evidence with an official or independently
   reviewed source where possible;
3. confirming the entry contains no case files, source path, golden value, or
   target-specific parameter set;
4. recording provenance, SHA256, and redistribution license;
5. rerunning corpus, leakage, retrieval, and manifest tests;
6. creating a new protocol freeze before formal evaluation.

Benchmark-private source mappings, validators, golden results, and official
baseline observations are never part of this corpus.
