# Example Thematic Workspace

This folder shows the minimal external data workspace that the runner expects.

The runner repo itself should stay generic. Topic-specific data lives here instead:

- editorial identity
- backlog topics
- evidence index
- work queue
- runtime reports
- active work items
- archive

You can copy this folder to start a new niche or site, then replace:

- `runner-config.yaml`
- `strategy/topic-backlog.yaml`
- `evidence-bank/evidence-index.yaml`

The rest of the runner code does not need to change.
