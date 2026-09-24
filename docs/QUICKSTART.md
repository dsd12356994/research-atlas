# Getting started / 使用指南

## Demo versus real knowledge

`python -m hub.setup --demo` creates six **fictional** source cards and three illustrative question cards. It performs no network calls and refuses a nonempty library. These are UI fixtures; the answer generator refuses to use them as research evidence. Do not treat the demo as a literature benchmark.

For real work use `python -m hub.setup` in a separate checkout. This initializes an empty library and graph. Nothing runs on a schedule until you explicitly configure a scheduler.

## Model service (optional)

Use a service supporting `/chat/completions`, `messages`, `max_tokens`, and `finish_reason`. Providers differ; compatibility with every service is not promised. From the activated environment:

```powershell
$env:ATLAS_API_KEY = 'your-key'
$env:ATLAS_BASE_URL = 'https://your-provider.example/v1'
$env:ATLAS_MODEL = 'your-chat-model'
python -m hub.server --port 8767
```

On macOS/Linux, use `export ATLAS_API_KEY=...` and equivalent variables. Do not put real keys in Git, screenshots or issue reports. These environment variables belong to the server process; restart it after changing them.

Alternatively, copy `model.example.json` **outside the repository**, put your service settings there, copy `runtime.example.json` to the ignored `runtime.local.json`, and set `credential_path` to that file. Never use the placeholder URL as a real endpoint.

Some providers require extra request fields. Put only those fields in `runtime.local.json` under `extra_body`, for example `{"thinking":{"type":"disabled"}}`. This is optional and provider-specific. Truncated or malformed responses fail instead of becoming valid research notes.

Manual model questions and synthesis calls are separate from the daily reading budget. Opening, refreshing and navigating the UI do not call the model. There is no backend on the GitHub Pages demo.

## Collect and read

Edit topics and source queries in `config.json`. The provided configuration focuses on retrieval, multimodal systems, efficiency and privacy; it is an example, not comprehensive coverage of AI.

```bash
python -m hub.pipeline run --deep 0        # metadata collection, no model
python -m hub.pipeline search "audio retrieval"
python -m hub.pipeline run --deep 2        # at most two selected chunks this run
python -m hub.wiki build                  # deterministic wiki compilation
python -m hub.wiki lint
```

The reading-budget day and dated reports currently use UTC+08:00, even if the OS scheduler uses another timezone. Align your schedule explicitly. The configured daily reading limit is currently five **calls**, including failures; it is not five complete papers. A selected paper receives one chunk per run. Inspect extraction and chunk coverage in the UI. Exit codes: 0 complete for the declared run scope; 2 partial/resumable; 1 failed. A partial exit is not evidence that all sources are complete.

Initial history, overlap, page/time limits and reconciliation are in `config.json`. arXiv/Crossref sources support persistent pagination. RSS/Hugging Face are snapshots. Citation chasing stays disabled pending dedicated validation; OpenReview and ACL are not standalone collectors in this release.

## Add and reuse knowledge

```bash
python -m hub.wiki ingest notes.md --title "My source notes"
python -m hub.wiki ask "这些方法的适用条件有哪些差别？" --keywords "federated retrieval"
python -m hub.wiki synthesize "Federated retrieval"
```

Notes can use `[[concepts/federated-retrieval|联邦检索]]` links. Add a source URL when available. User notes are not automatically verified. Keep the generated JSON-valued frontmatter valid when editing Markdown manually.

The browser's **向知识库提问** button performs the saved-Wiki workflow. The older `hub.pipeline ask` command instead writes a paper-library answer under `reports/answers/`; it does not use the saved-Wiki pathway.

Open `knowledge/wiki` as an Obsidian vault. `index.md` is the entry point, `log.md` records maintenance. Exports include BibTeX, graph JSON and an offline HTML view. Exports contain your data too—review them before sharing.

## Optional daily schedule

Windows: after testing a manual run, execute `scripts/register_daily.ps1` in PowerShell. It creates `ResearchAtlas-Daily` at 09:00 **in the machine's local timezone**, refuses to overwrite an existing task and uses an interactive user session. The machine must be awake/logged in and connected. To use a scheduled model, use the external credential-file option; a terminal's temporary environment variables will not transfer.

macOS/Linux: schedule an equivalent command with your preferred scheduler, using absolute paths and the intended timezone. For example, replace both paths before adding this cron line:

```cron
0 9 * * * cd /absolute/path/research-atlas && /absolute/path/research-atlas/.venv/bin/python -m hub.pipeline run --deep 0 >> /absolute/path/atlas-daily.log 2>&1
```

No cloud execution or notification service is configured automatically. Use `python scripts/backup_database.py` for a consistent SQLite backup. Do not run the same synced SQLite database concurrently on two machines.
