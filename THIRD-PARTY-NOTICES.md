# Third-party provenance

## Cytoscape.js

Runtime graph library, **MIT**, tag `v3.33.1`.
https://github.com/cytoscape/cytoscape.js/tree/v3.33.1

Bundled, unmodified files: `web/assets/cytoscape.min.js`, `web/assets/cytoscape.LICENSE`. Checksums are in `sources.lock.json`.

## Prior

Only `src/prior/models.py` and `src/prior/__init__.py` are copied for compatible data exports, **Apache-2.0**, commit **`195f8445b5ea1edddc01ab232995f4d6b0b1b53d`** as recorded in `sources.lock.json`.

https://github.com/Agents4Academia-AI/prior

Files remain under `third_party/prior/`, with upstream `LICENSE` and `NOTICE`. Research Atlas does not run Prior's full inference or database service. Project MIT licensing does not replace this dependency's Apache license.

## Design references

- [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): raw/wiki/schema workflow; no endorsement or official affiliation.
- [Quartz Graph View](https://quartz.jzhao.xyz/features/graph-view): local graph and backlink navigation reference; no Quartz runtime code bundled.
- [Sigma.js](https://www.sigmajs.org/docs/): visualization alternative reviewed; not a runtime dependency.
- [Research Radar](https://github.com/ramazan793/research-radar): initial collector/model-adapter reference in the private prototype. Those imports were replaced with this project's bounded collectors and completion checks; no upstream code is included in this release.

Python and optional browser-test packages retain their respective upstream licenses. The demonstration fixtures were written for this project and do not reproduce a private library or real paper text.
