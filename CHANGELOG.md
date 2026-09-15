# Changelog

## Unreleased

### Added

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
