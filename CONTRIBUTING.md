# Contributing

Start with a small, reproducible issue. Describe the task, what you expected, the actual result, Python/OS/browser versions, and a **synthetic or public** minimal fixture. Never attach credentials, a personal database or unpublished research without checking what it contains.

Run `python -m unittest discover -s tests -v`. For UI changes, also run the synthetic-demo browser smoke test described in the README and attach a screenshot using demo data. Keep model calls opt-in; CI must not require secrets or paid inference.

Preserve the distinction between metadata, text extraction, partial reading and verified evidence. Never label a keyword link as a proven causal connection. Keep borrowed code's license and provenance. Explain whether a change touches storage, protocol, model requests or only presentation.

Use focused branches and pull requests. English and Chinese reports are welcome. Proposed additions should include a concrete workflow or failure they improve, not just another integration name.
