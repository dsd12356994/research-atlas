# Research Atlas

**Turn scattered papers into a source-linked research wiki.**

[![CI](https://github.com/dsd12356994/research-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/dsd12356994/research-atlas/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)
[![MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[中文说明](README.zh-CN.md) · [Try the read-only demo](https://dsd12356994.github.io/research-atlas/) · [Getting started](docs/QUICKSTART.md) · [Architecture](docs/ARCHITECTURE.md)

Collect literature, keep track of what was actually read, connect sources to questions, and save useful AI answers as Markdown. Inspired by [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), implemented as a local Python application.

![Research Atlas graph — synthetic demonstration data](docs/assets/graph.png)

> **Early public preview.** The UI and generated notes are primarily in Chinese; technical terms stay in English. Documentation is bilingual. The public demo uses explicitly fictional fixtures, not the maintainer's library or real research findings.

## Why use it?

- **Know what you have read.** Metadata, archived documents and partially read chunks stay distinct. Failed or unfinished downloads are not silently treated as complete.
- **Keep knowledge outside chat history.** Source cards, topic pages, research questions and saved answers live in Markdown, with an index, backlinks and an append-only maintenance log.
- **Explore traceable connections.** Filter and drag the Cytoscape graph, inspect a source, expand statements, focus on neighbors, or export a PNG. Links have explicit meanings; they are not invented causal relationships.
- **Save answers with their inputs.** Ask over existing wiki excerpts using your own compatible Chat Completions service. Each answer item must cite an input page. Generated answers do not feed themselves back as independent evidence.
- **Notice when knowledge ages.** Input fingerprints flag stale synthesis pages; compilation preserves manual edits. Lint checks broken links and snapshot integrity.
- **Start without an API key.** The demo, browsing, note ingestion, graph and metadata-only collection do not call a model.

## Quick start

Python 3.10+ and Git. Run from the repository root.

```bash
git clone https://github.com/dsd12356994/research-atlas.git
cd research-atlas
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m hub.setup --demo
python -m hub.server --port 8767
```

Open **http://127.0.0.1:8767**. Initialization makes no network or model calls. The browser runs locally, with bundled graph assets.

For a **real library**, use a separate clean checkout and run `python -m hub.setup` without `--demo`. Demo initialization refuses to overwrite existing work. [Set up your model and collection](docs/QUICKSTART.md).

## One small daily loop

1. Collect metadata: `python -m hub.pipeline run --deep 0`.
2. Read the collector status; pick a specific research question and a few sources.
3. Add your own `.md` / `.txt` reading notes with `python -m hub.wiki ingest path/to/note.md --title "My reading note"`.
4. Inspect sources and backlinks in the graph. With a model configured, ask a focused question in the UI and keep the answer in the wiki.
5. Check `python -m hub.wiki lint` before trusting or sharing a synthesis.

Automatic collection covers configured arXiv queries and categories, Crossref title queries, and RSS/Hugging Face snapshots. Coverage is tracked per configured query and time window—not as a claim of complete coverage of all research.

## How it fits with research_agent

[research_agent](https://github.com/dsd12356994/research_agent) organizes a research project through hypothesis design, experiments, drafting and review. **Research Atlas is the ongoing literature and knowledge workspace before and alongside that process.** They can be used independently; currently you hand off Markdown notes and source links, rather than relying on a hidden integration.

## Boundaries worth understanding

- Citation identity and quote-existence checks do **not** prove that an interpretation is correct. AI output remains a draft.
- A completed text-extraction pass is not a promise of complete OCR, figure understanding or full-paper reading.
- Topic links come from rules and explicit wiki links. This release does not implement automatic contradiction detection, multi-hop GraphRAG or research novelty verification.
- Local storage does not imply local inference: when you submit an AI request, selected source excerpts go to your configured model provider.
- Browsing and metadata collection are key-free. Model reading and questions may incur provider charges. The configured automatic reading limit does not cover manual questions.
- The server is a single-user loopback application, not an Internet-facing multi-user service.

## Development

```bash
python -m unittest discover -s tests -v
# Optional browser checks:
npm ci
npx playwright install chromium
python -m hub.setup --demo   # a fresh checkout only
node scripts/smoke_public.cjs
```

See [contributing](CONTRIBUTING.md), [data and provider boundaries](SECURITY.md), and the [roadmap](docs/ROADMAP.md). Useful issues include a reproducible import failure, a misleading coverage label, or a concrete workflow that is hard to finish.

If this fits your research workflow, a star helps others find it. Feedback and small, reproducible contributions are especially welcome.

## Credits and license

Project code is MIT. [Cytoscape.js](https://github.com/cytoscape/cytoscape.js) is bundled under MIT; selected [Prior](https://github.com/Agents4Academia-AI/prior) data models are bundled under Apache-2.0. Upstream licenses and notices are retained. [Third-party provenance](THIRD-PARTY-NOTICES.md).

Karpathy's gist is a design reference, not an endorsement or an official distribution.
