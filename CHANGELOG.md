# Changelog

## Unreleased

### Added

- Ask can fetch one document's indexed body via ``POST /admin/document-content``
  (OpenSearch ``document_id``, ACL + tenant filters matching admin search,
  16-chunk / 24k-char cap, ``truncated`` flag). ACL miss returns ``found:
  false``. Body text is not logged. ``list_documents_matching`` rows now include
  ``document_id``.

### Fixed

- Folded comments on Confluence pages, Jira issues, Linear issues, and ClickUp
  tasks now prefix each comment with the speaker display name (``Name: text``,
  ``Unknown`` when unresolved) via a shared ``format_attributed_message`` helper.
  Re-index those sources from beginning after deploy. GitHub comments are not
  fetched today (unchanged). Zendesk/Asana keep their existing ``Comment by``
  lines. Slack/Teams/Discourse wait for the next batch.

- Slack thread replies were dropped on a cold reindex when
  ``conversations.replies(parent)`` returned the parent only (or the parent
  fetch failed). History lookup now uses the row's own ``ts`` first, merges a
  parent refetch into that payload, and still skips minting ``channel__reply_ts``
  when the parent thread cannot be loaded.

### Added

- Slack Ask field schema (POC): Slack declares optional tags ``channel`` and
  ``author`` in ``FIELD_SCHEMA``. ``POST /admin/document-fields`` with
  ``source=slack`` returns that schema intersected with the connector instance
  ``indexed_field_selection`` (NULL = both tags, ``[]`` = none). Other sources
  still use ``ALLOWED_TAG_KEYS``. Unchecking a tag does not purge stored tags
  or force a re-index. No onboarding checkbox UI in this pass. Alembic merge
  ``d8a41c2e9f70`` joins this migration with ``has_been_indexed`` so
  ``upgrade head`` has a single head.



- Admin ``POST /admin/search`` accepts optional ``retrieval=hybrid`` (default
  ``keyword``). Hybrid embeds the query with current SearchSettings and calls
  ``hybrid_retrieval(include_hidden=True)``. Empty query still uses random
  retrieval. Connectors work-items omit the field and stay keyword.

- GitLab issues and merge requests now tag Ask list fields from GitLab REST
  data: ``status``/``state`` (opened/closed/merged), ``created``, ``updated``,
  ``assignee`` when assigned, ``duedate`` when set, ``labels``, ``project``,
  ``repo`` (same tag as GitHub), ``reporter``. GitLab has no native priority — that tag is omitted, not
  invented. Empty assignee/due stay untagged. Re-index from beginning after
  deploying the engine.

- Jira connector indexes populated custom fields (site field name + value)
  into ticket text and ``custom_fields`` metadata. Rank / lexorank is skipped.
  Field names come from the Jira field catalog — not a hardcoded tenant list.
  ``custom_fields`` is returned on document-by-key and is not a count/filter field.
  Restricted issues are never fetched: JQL only returns what the token can read.

- GitHub connector indexes a repository overview (description, default-branch
  commit count, branch names, contributors, open / merged / closed-without-merge
  PR snapshot counts, last-commit additions/deletions) and the README. Closed
  without merging uses GraphQL ``CLOSED`` or list ``merged_at``, not REST
  ``state=closed`` (which includes merged). Ask can filter PRs by `state` and
  `merged`.
- GitHub connector indexes one document per unique commit SHA across all
  branches (message, files touched, lines added/removed, file names). Diffs
  (patch) are never stored. Same SHA on two branches is one document. Ask
  filters with ``object_type=Commit``. This is Ask context only and does not
  update Weighted Risk.
- GitHub connector rejects a valid token that cannot read repositories.
  Classic PATs need ``repo`` or ``public_repo``; fine-grained PATs need
  Contents: Read. A contents ``404 Not Found`` is missing permission;
  only GitHub's empty-repository message is allowed through. The error
  names that missing scope/permission.

### Fixed

- ``POST /admin/document-list`` accepts a named ``source`` with no tag or date
  filter and returns that connector's indexed documents, capped at 50.
  ``source=all`` with no filter is still rejected.

- Teams threads now tag ``channel`` with the Graph display name and ``author``
  with the message sender's Graph display name when present (same keys as Slack).
  Unknown / missing senders stay untagged. Email is never stored. Only documents
  indexed after this engine deploy get the tags. Sync Now and a normal
  index-from-beginning will not backfill the existing corpus: ``content_hash()``
  covers ``doc_metadata``, not the tag dict. Forced reprocess: targeted reindex
  that bypasses both the timestamp gate and the content-hash gate, or
  delete/recreate the Teams connector.

- Teams indexing no longer fails the whole connector when Graph lists a team
  but ``GET /teams/{id}/channels`` returns 404 (``No threadId found for TeamId``).
  That team is skipped; other teams still index. 401/403 still fail the run.
  Rebuild the engine and re-run Teams sync.

- Slack indexing no longer fails the whole connector on ``conversations.join``.
  Private channels the bot is not in are skipped (join is invite-only). Public
  join failures that are not a dead token (missing ``channels:join``, archived,
  restricted) skip that channel so indexing can continue on channels the bot
  can already read. Invalid/revoked tokens still fail the run.

- Slack conversations.history rows that are thread replies but omit top-level
  ``thread_ts`` (permalinks without ``?thread_ts=``) now resolve the parent via
  ``conversations.replies`` instead of minting a second document keyed by the
  reply ts. ``thread_broadcast`` is not globally skipped. Rebuild the engine and
  prune the Slack connector so extra reply-id documents can drop.

- Ask ``list_documents_matching`` rows include the connector ``source`` id so
  answers can attribute Slack vs Jira (and other sources) instead of blending
  them. Rebuild the engine for per-row source on ``source=all`` lists.

- Jira assignee/reporter tags now use `displayName` from the issue payload, the
  same as the original connector-engine. Email is an optional extra field read
  only when already present; it is never required to capture the name. Syncs
  log source-to-tag and persisted-tag completeness, and reject partial
  bulk-fetch responses.
- Jira comments are fully paginated, ADF mentions/status/emoji/links become
  readable text, title/description/comments are read from the issue payload
  independently, and Jira 429s retry with Retry-After / backoff.
- Ask can list exact ticket keys for a filter (label, assignee, status, …).
  Counts no longer strand the model without IDs.
- Catalog queries use a published field allow-list (including parent and
  duedate). Names use contains; key/parent/status/dates are exact. Find/count
  support AND filters. Email tags are never queryable or returned.
