---
description: Enforce resource handling, security, timeouts, pagination, and error-handling standards
alwaysApply: true
---

# Coding Standards

Use simple, proven engineering patterns and keep production behavior explicit.

## Resource Management

- Use context managers for all resources: DB connections, file handles, HTTP clients/sessions, locks, and temporary resources.
- Prefer `with` or `async with` blocks. Do not leave resources to implicit cleanup.

## Error Handling And Logging

- Catch specific exceptions only, such as `ValueError`, `ConnectionError`, and `TimeoutError`.
- Do not swallow errors. Return proper error responses or re-raise with useful context.
- Log with full operational context: `correlation_id`, `user_id`, `operation`, and the relevant resource or external dependency.
- Avoid blanket `except Exception` unless it is at a process or request boundary and logs context before returning a controlled response.

## Security Requirements

- Use parameterized queries only. Never build SQL with string interpolation, concatenation, f-strings, or template substitution.
- Validate all inputs with Pydantic models before business logic runs.
- Sanitize and normalize file paths to prevent directory traversal. Keep file operations inside an approved base directory.
- Add rate limiting to public endpoints.

## External Calls And Timeouts

- Every external call must have an explicit timeout.
- HTTP requests: 5 second connect timeout and 30 second read timeout.
- Database queries: 200 millisecond timeout.
- Cache operations: 100 millisecond timeout.

## Pagination And Memory

- Use cursor-based pagination for list endpoints and batch processing.
- Enforce a maximum page size of 100 items.
- Document memory implications in comments when loading collections, buffering responses, or processing batches.
- Prefer streaming, pagination, or bounded batches over loading unbounded data into memory.

**ALWAYS** flag inconsistencies or contradictions between what is being asked from you and what you have in your rules.