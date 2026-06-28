
import * as crypto from "crypto";
import { fetch } from "undici";
import * as vscode from "vscode";

import type { IOperation, IOperationFilter, IStorage } from "../types";
import { encryptJSON } from "../utils/encryption";
import { safeError, safeWarn } from "../utils/sanitize";

// ============================================================================
// ============================================================================

export interface CloudStorageOptions {
	serverUrl: string;
	enabled?: boolean;
	maxRetries?: number;
	initialRetryDelayMs?: number;
	batchSize?: number;
	timeoutMs?: number;
	maxQueueSize?: number;
}

interface SessionMeta {
	userId: string;
	projectId: string;
	sessionId: string;
	projectName: string;
	workspacePath: string;
}

interface SyncOperationsRequest {
	ft_token: string;
	meta_datas: {
		project_id: string;
		project_name: string;
		session_id: string;
		workspace_path: string;
	};
	operations: Array<{
		id: number;
		operationType: string;
		role: string;
		timestamp: number;
		endTimestamp: number;
		readableDetails: string;
		[key: string]: unknown;
	}>;
}

interface SyncOperationsResponse {
	status: "ok" | "error";
	count?: number;
	message?: string;
}

interface EncryptedOperation {
	id: number;
	encryptedData: string;
	timestamp: number;
}

// ============================================================================
// ============================================================================

export class CloudStorage implements IStorage {
	readonly name = "cloud";

	private readonly serverUrl: string;
	private readonly maxRetries: number;
	private readonly initialRetryDelayMs: number;
	private readonly batchSize: number;
	private readonly timeoutMs: number;
	private readonly maxQueueSize: number;

	private enabled: boolean;
	private sessionMeta: SessionMeta;

	private encryptedQueue: EncryptedOperation[] = [];
	private isUploading: boolean = false;
	private pendingUpload: boolean = false;

	constructor(options: CloudStorageOptions) {
		this.serverUrl = options.serverUrl.replace(/\/$/, "");
		this.enabled = options.enabled ?? false;
		this.maxRetries = options.maxRetries ?? 3;
		this.initialRetryDelayMs = options.initialRetryDelayMs ?? 1000;
		this.batchSize = options.batchSize ?? 50;
		this.timeoutMs = options.timeoutMs ?? 30000;
		this.maxQueueSize = options.maxQueueSize ?? 100;

		this.sessionMeta = this._initSessionMeta();
	}

	// ========================================================================
	// ========================================================================

	setUserId(userId: string): void {
		this.sessionMeta.userId = userId;
	}

	setEnabled(enabled: boolean): void {
		this.enabled = enabled;
	}

	getSessionId(): string {
		return this.sessionMeta.sessionId;
	}

	getProjectId(): string {
		return this.sessionMeta.projectId;
	}

	// ========================================================================
	// ========================================================================

	write(operation: IOperation): void {
		if (!this.enabled) {
			return;
		}

		if (!this.sessionMeta.userId) {
			return;
		}

		try {
			const serialized = this._serializeOperation(operation);

			const jsonString = JSON.stringify(serialized);

			const encryptedData = this._encryptMemoryData(jsonString);

			const encryptedOp: EncryptedOperation = {
				id: operation.id,
				encryptedData: encryptedData,
				timestamp: operation.endTimestamp,
			};

			this.encryptedQueue.push(encryptedOp);


			if (this.encryptedQueue.length >= this.batchSize || this.encryptedQueue.length >= this.maxQueueSize) {
				this.pendingUpload = true;
			}
		} catch (error) {}
	}

	query(_filter?: IOperationFilter): IOperation[] {
		return [];
	}

	triggerUploadOnCompletionRequest(): void {
		if (this.pendingUpload && this.encryptedQueue.length > 0) {
			void this._processUploadQueue();
		}
	}

	async flush(): Promise<void> {
		await this._processUploadQueue();
	}

	dispose(): void {
		void this._processUploadQueue().catch(e => {
			safeError("[CloudStorage]", "Flush failed on dispose", e);
		});

		this._securelyWipeQueue();
	}

	// ========================================================================
	// ========================================================================

	private _initSessionMeta(): SessionMeta {
		const workspaceFolders = vscode.workspace.workspaceFolders;
		const workspacePath = workspaceFolders?.[0]?.uri.fsPath || "";

		const projectName = workspacePath.split(/[/\\]/).pop() || "unknown";

		const projectId = crypto.createHash("md5").update(workspacePath).digest("hex");

		const sessionId = crypto.randomUUID();

		return {
			userId: "",
			projectId,
			sessionId,
			projectName,
			workspacePath,
		};
	}

	private async _processUploadQueue(): Promise<void> {
		if (this.isUploading || this.encryptedQueue.length === 0) {
			return;
		}

		if (!this.enabled || !this.sessionMeta.userId) {
			return;
		}

		this.isUploading = true;

		try {
			const batch = this.encryptedQueue.splice(0, this.batchSize);

			if (batch.length > 0) {
				await this._uploadEncryptedBatch(batch);

				for (let i = 0; i < batch.length; i++) {
					(batch[i] as any).encryptedData = "";
				}
			}
		} catch (error) {
			safeError("[CloudStorage]", "Upload failed", error);
		} finally {
			this.isUploading = false;
		}

		if (this.encryptedQueue.length > 0) {
			await this._processUploadQueue();
		}

		if (this.encryptedQueue.length < this.batchSize && this.encryptedQueue.length < this.maxQueueSize) {
			this.pendingUpload = false;
		}
	}

	private async _uploadEncryptedBatch(encryptedBatch: EncryptedOperation[]): Promise<void> {
		const operations: SyncOperationsRequest["operations"] = [];

		for (const encOp of encryptedBatch) {
			try {
				const decrypted = this._decryptMemoryData(encOp.encryptedData);
				const operation = JSON.parse(decrypted);
				operations.push(operation);
			} catch (error) {
				safeError("[CloudStorage]", `Failed to decrypt operation ${encOp.id}`, error);
			}
		}

		const requestBody: SyncOperationsRequest = {
			ft_token: this.sessionMeta.userId,
			meta_datas: {
				project_id: this.sessionMeta.projectId,
				project_name: this.sessionMeta.projectName,
				session_id: this.sessionMeta.sessionId,
				workspace_path: this.sessionMeta.workspacePath,
			},
			operations: operations,
		};

		const encryptedData = encryptJSON(requestBody);

		const encryptedRequestBody = {
			encrypted: true,
			data: encryptedData,
		};

		await this._uploadWithRetry(encryptedRequestBody);
	}

	private _encryptMemoryData(data: string): string {
		const key = this.sessionMeta.sessionId;
		const buffer = Buffer.from(data);
		const encrypted = Buffer.alloc(buffer.length);

		for (let i = 0; i < buffer.length; i++) {
			const keyChar = key.charCodeAt(i % key.length) || 0;
			const bufferByte = buffer[i];
			if (bufferByte !== undefined) {
				encrypted[i] = bufferByte ^ keyChar;
			}
		}

		return encrypted.toString("base64");
	}

	private _decryptMemoryData(encrypted: string): string {
		const key = this.sessionMeta.sessionId;
		const buffer = Buffer.from(encrypted, "base64");
		const decrypted = Buffer.alloc(buffer.length);

		for (let i = 0; i < buffer.length; i++) {
			const keyChar = key.charCodeAt(i % key.length) || 0;
			const bufferByte = buffer[i];
			if (bufferByte !== undefined) {
				decrypted[i] = bufferByte ^ keyChar;
			}
		}

		return decrypted.toString("utf-8");
	}

	private _securelyWipeQueue(): void {
		try {
			for (let i = 0; i < this.encryptedQueue.length; i++) {
				const op = this.encryptedQueue[i];
				(op as any).encryptedData = "";
				(op as any).id = 0;
				(op as any).timestamp = 0;
			}
			this.encryptedQueue = [];
		} catch (error) {
			safeError("[CloudStorage]", "Failed to wipe queue", error);
		}
	}

	private _serializeOperation(op: IOperation): SyncOperationsRequest["operations"][0] {
		const serialized: SyncOperationsRequest["operations"][0] = {
			id: op.id,
			operationType: op.operationType,
			role: op.role,
			timestamp: op.startTimestamp,
			endTimestamp: op.endTimestamp,
			readableDetails: op.readableDetails,
		};

		const opAny = op as unknown as Record<string, unknown>;

		const extraFields = [
			"commandLine",
			"output",
			"exitCode",
			"durationMs",
			"terminalName",
			"terminalId",
			"query",
			"searchId",
			"scope",
			"totalMatches",
			"totalFiles",
			"filePath",
			"line",
			"column",
			"debugSessionId",
			"debugType",
			"command",
			"dapType",
			"languageId",
			"oldPath",
			"newPath",
		];

		for (const field of extraFields) {
			if (field in opAny && opAny[field] !== undefined) {
				serialized[field] = opAny[field];
			}
		}

		return serialized;
	}

	private async _uploadWithRetry(requestBody: SyncOperationsRequest | { encrypted: boolean; data: string }): Promise<void> {
		let delay = this.initialRetryDelayMs;

		for (let attempt = 0; attempt < this.maxRetries; attempt++) {
			try {
				const response = await fetch(`${this.serverUrl}/sync_operations`, {
					method: "POST",
					headers: {
						"Content-Type": "application/json",
					},
					body: JSON.stringify(requestBody),
					signal: AbortSignal.timeout(this.timeoutMs),
				});

				if (!response.ok) {
					throw new Error(`HTTP ${response.status}: ${response.statusText}`);
				}

				const result = (await response.json()) as SyncOperationsResponse;

				if (result.status === "ok") {
					return;
				}

				throw new Error(result.message || "Unknown error");
			} catch (error) {
				const isLastAttempt = attempt === this.maxRetries - 1;

				if (isLastAttempt) {
					safeError("[CloudStorage]", `Upload failed after ${this.maxRetries} attempts`, error);
					return;
				}

				safeWarn("[CloudStorage]", `Upload attempt ${attempt + 1} failed, retrying in ${delay}ms`, error);

				await this._sleep(delay);
				delay *= 2;
			}
		}
	}

	private _sleep(ms: number): Promise<void> {
		return new Promise(resolve => setTimeout(resolve, ms));
	}
}
