# Sanitization Helper Examples

## Quick Start

```typescript
import { safeError, safeWarn, safeLog, sanitizeError } from "./utils/sanitize";

safeError("[Module]", "Operation failed", error);
safeWarn("[Module]", "Queue is full", { queueSize: 100 });
safeLog("[Module]", "User logged in", { userId: "user123456789" });
```

## Examples

### Error Objects

Input:

```typescript
const error = new Error("Failed to connect to https://api.assistant.example.com with token eyJhbGciOiJIUzI1...");
safeError("[CloudStorage]", "Upload failed", error);
```

Sanitized output:

```text
[CloudStorage] Upload failed: {
  name: "Error",
  message: "Failed to connect to https://api.assistant.example.com with token eyJhbG***",
  stack: "Error: Failed to connect...\n    at .../src/..."
}
```

### User Data Objects

Input:

```typescript
const userData = {
  userId: "user123456789",
  token: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  sessionId: "550e8400-e29b-41d4-a716-446655440000",
  projectId: "d99951e3cbcf57795453395cc805c6e2",
  operationCount: 50
};
safeLog("[Module]", "User data", userData);
```

Sanitized output:

```text
[Module] User data: {
  operationCount: 50
}
```

Sensitive fields such as `userId`, `token`, `sessionId`, and `projectId` are removed from object output.

### File Paths

Input:

```typescript
safeError("[FileCollector]", "Failed to read /workspace/project/lib/extension/src/file.ts");
```

Sanitized output:

```text
[FileCollector] Failed to read .../extension/src/file.ts
```

## Sanitization Rules

| Type | Rule | Example |
| --- | --- | --- |
| Token in strings | Keep the first 6 characters | `eyJhbG***` |
| UUID in strings | Keep the first 8 characters | `550e8400-****` |
| File paths | Keep the last two path segments | `.../src/file.ts` |
| Sensitive object fields | Remove the field entirely | Field is absent from output |

Sensitive object fields include `token`, `ft_token`, `password`, `secret`, `apiKey`, `userId`, `user_id`, `sessionId`, `session_id`, `projectId`, and `project_id`.

## API Reference

### `safeError(prefix: string, message: string, error?: any)`

Logs sanitized error data.

### `safeWarn(prefix: string, message: string, data?: any)`

Logs sanitized warning data.

### `safeLog(prefix: string, message: string, data?: any)`

Logs sanitized informational data.

### `sanitizeError(error: any): any`

Returns a sanitized copy of an error-like value.

## Migration

Before:

```typescript
console.error("[CloudStorage] Upload failed:", error);
```

After:

```typescript
safeError("[CloudStorage]", "Upload failed", error);
```

Sanitization is applied in development and production logs.
