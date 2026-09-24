# Data and security boundaries

This is an early, single-user local application. The HTTP server binds to 127.0.0.1 and must not be exposed through a public tunnel or reverse proxy without adding and reviewing authentication and isolation.

API keys come from explicit ATLAS_* environment variables or an operator-selected credential file. They are not returned to the browser. Selected source excerpts and questions are sent to the configured provider when a model operation is requested; source snapshots and model outputs remain in local ignored directories.

Raw snapshots, downloaded papers, notes, answers, exports, backups and logs may contain private material. Git ignore rules reduce accidental publication but do not make those files non-sensitive. Review every export before sharing. The public demo contains synthetic examples only.

Source content and model output are untrusted data. They are never executed as instructions or shell commands. Citation-ID checks and snapshot hashes are not semantic truth checks.

Use GitHub private vulnerability reporting when available. If unavailable, open a minimal issue asking for a private reporting channel without posting exploitable details or secrets. No formal security audit or hosted-service guarantee is claimed.
