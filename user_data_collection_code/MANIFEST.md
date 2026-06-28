# User Data Collection Code Subset

Source: anonymized internal IDE-assistant repository snapshot.

This directory contains the source files related to user-operation collection,
upload to backend storage, project-code sync, and adjacent frontend telemetry.
It intentionally excludes `.env*`, `.git`, `node_modules`, `dist`, `build`, and
generated/user data files.

## Reuse notes

This subset is an anonymized reference implementation of the collection layer,
not a drop-in standalone product. Several data sources are wired to the host IDE
assistant implementation that originally owned the chat UI, agent runtime,
authentication, inline-completion provider, feature flags, and backend APIs.

If you reuse this code in another IDE assistant, replace the host-specific
adapters before collecting data:

- Chat and agent interaction events:
  - replace webview RPC handlers and message-center calls with your own chat UI
    and agent runtime interfaces;
  - make sure user inputs, assistant responses, tool calls, and agent state
    changes are converted into the operation schema used by
    `OperationCollection`.
- Inline-completion events:
  - replace the completion provider integration with your own autocomplete
    provider;
  - emit accepted/rejected completion events through the collectors in
    `operation_collection`.
- Authentication and user identity:
  - replace token/user-id storage and login hooks with your own identity layer;
  - do not log raw tokens, user ids, session ids, or project ids.
- Backend upload endpoints:
  - replace all `assistant.example.com` placeholder URLs with your own ingestion
    service;
  - keep the existing sanitization/encryption boundary or an equivalent one.
- Feature flags and analytics:
  - replace PostHog/Sentry wiring with your own telemetry controls, or disable
    them for a fully local collection setup.

## Core operation collection

- `lib/extension/src/operation_collection/operationCollection.ts`
  - main singleton
  - enables/disables collectors
  - sets user id
  - buffers/merges operations
  - sends operations to `CloudStorage`
  - triggers project sync through `ProjectSyncer`
- `lib/extension/src/operation_collection/collectors/`
  - VS Code event collectors:
    - editor edits, cursor/selection, view changes
    - file create/delete/rename/workspace changes
    - terminal open/close/state
    - terminal shell execution and output
    - clipboard polling
    - debug session events/output
    - accepted inline completions
    - explicit user requests
- `lib/extension/src/operation_collection/operations/`
  - operation schemas and readable detail serialization
- `lib/extension/src/operation_collection/storage/CloudStorage.ts`
  - encrypted operation queue
  - uploads batches to `${serverUrl}/sync_operations`
- `lib/extension/src/operation_collection/sync/ProjectSyncer.ts`
  - scans workspace files with size/count/depth filters
  - respects `.gitignore`
  - posts project structure to `${serverUrl}/sync_repo_structure`
  - zips and posts changed file contents to `${serverUrl}/sync_repo_content`
- `lib/extension/src/operation_collection/utils/`
  - upload encryption and log sanitization helpers

## Integration points

- `lib/extension/src/extension.ts`
  - initializes `OperationCollection` with logged-in user id
  - updates user id on login changes
  - records accepted inline completions
- `lib/extension/src/utils/uploadProject.ts`
  - toggles `OperationCollection` via PostHog feature flag
- `lib/extension/src/completion/CompletionItemProvider.ts`
  - calls `triggerUploadOnCompletionRequest()` before completion requests
- `lib/extension/src/agentServerWeb/receiver.ts`
  - exposes webview RPC handlers:
    - `operationCollectionSetAvailable`
    - `operationCollectionAddUserInput`
- `webUI/src/components/bottomInputArea/bottomInputArea.tsx`
  - sends user message text to extension-side operation collection
- `webUI/src/tracker/`
  - frontend click/error/custom telemetry queue in localStorage
  - reports queued tracker data through `requestHttp`
- `webUI/src/views/conversation/conversation.tsx`
  - contains conversation share storage path via `/codeuser/store`

## Dependency/context files

- `lib/extension/src/utils/GlobalVariables.ts`
- `lib/extension/src/utils/ServerURL.ts`
- `lib/extension/src/utils/PlatformInfoCollector.ts`
- `lib/extension/src/utils/request_fetch.ts`
- `lib/extension/src/local_analyze/utils.ts`
- `lib/extension/src/globalConfig.ts`
- `webUI/src/api/index.ts`
- `webUI/src/api/oldFetch.ts`
- `webUI/src/api/urls.ts`
- `webUI/src/globalConfig.ts`
- `webUI/src/utils/postHogProvider.ts`
- package and tsconfig metadata for the selected extension/webUI workspace

## Safety check performed

- Excluded environment/config secret files.
- Excluded dependency/build/generated directories.
- Ran a local grep for private keys, common cloud keys, OpenAI-style keys,
  hardcoded API keys, passwords, and access/refresh tokens.
- The only cryptographic key in the selected code is the public RSA key in
  `operation_collection/utils/encryption.ts`, used for upload encryption.
