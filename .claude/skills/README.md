# Project-scoped skills

Skills committed here are auto-discovered by Claude Code for this repository
(`<project>/.claude/skills/<name>/`). They load in every session — including
ephemeral Claude-Code-on-the-web containers — without any per-session install
step. This is why they are vendored into the repo rather than installed to the
per-user `~/.claude/skills/` path (which does not survive container recycling).

## Provenance

The skills below are **vendored copies** from
[`scarson/agent-skills`](https://github.com/scarson/agent-skills) (private;
MIT-licensed, authored by the repo owner). They were copied from the
`plugins/*/skills/` tree of that repo, flattened into this directory.

| Source plugin     | Skills |
|-------------------|--------|
| `project-setup`   | `claude-agents-md-init`, `git-strategy-init`, `pitfalls-docs-init`, `project-init` |
| `superpowers-plus`| `bug-hunt-cycle`, `bug-hunter-differential`, `bug-hunter-exploratory`, `bug-hunter-holistic`, `bug-hunter-multipass`, `build-robust-features`, `handoff`, `health-review-cycle`, `performance-audit`, `performance-audit-cycle`, `plan-review-cycle`, `project-health-review`, `writing-plans-enhanced` |
| `utility`         | `url-to-markdown` |

`superpowers-plus` builds on the upstream **superpowers** plugin
([`obra/superpowers`](https://github.com/obra/superpowers)), which is installed
separately via the plugin marketplace — see
`extraKnownMarketplaces` / `enabledPlugins` in `../settings.json`.

## Updating

These are copies, not symlinks, so they do not track `scarson/agent-skills`
automatically. To refresh after upstream changes, re-copy the relevant
`plugins/*/skills/<name>/` directories over the ones here and commit.

> Note: `.claude/skills/gstack/` is `.gitignore`d (machine-local skill) and is
> intentionally not committed.
