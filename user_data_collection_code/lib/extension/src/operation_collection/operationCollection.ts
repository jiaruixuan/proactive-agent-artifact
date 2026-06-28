import { CollectorManager, CompletionCollector } from "./collectors";
import { type Operation, UserRequestOperation } from "./operations";
import { CloudStorage } from "./storage";
import { ProjectSyncer } from "./sync";
import { safeError } from "./utils/sanitize";

const SERVER_URL = "https://assistant.example.com/codeapi/try_completion";
const SYNC_INTERVAL_MS = 24 * 60 * 60 * 1000;

export class OperationCollection {
	private static instance: OperationCollection;
	private available = false;
	private readonly collectorManager: CollectorManager;
	private readonly _operations: Operation[] = [];
	private readonly cloudStorage: CloudStorage;
	private readonly projectSyncer: ProjectSyncer;
	private syncIntervalId: ReturnType<typeof setInterval> | null = null;
	private userId: string = "";

	private pendingOperation: Operation | null = null;
	// ==================================

	public static getInstance(): OperationCollection {
		if (!OperationCollection.instance) {
			OperationCollection.instance = new OperationCollection();
		}
		return OperationCollection.instance;
	}

	constructor() {
		this.cloudStorage = new CloudStorage({
			serverUrl: SERVER_URL,
			enabled: true,
			maxRetries: 3,
			initialRetryDelayMs: 1000,
		});
		this.projectSyncer = new ProjectSyncer({
			serverUrl: SERVER_URL,
			enabled: true,
		});
		this.collectorManager = new CollectorManager();
		this.collectorManager.onOperation(op => {
			if (this.available) {
				this.addOperation(op as unknown as Operation);
			}
		});
	}

	private _startSyncInterval(): void {
		if (this.syncIntervalId) {
			clearInterval(this.syncIntervalId);
			this.syncIntervalId = null;
		}
		this.syncIntervalId = setInterval(() => {
			if (this.userId) {
				this.projectSyncer.sync(this.userId).catch(e => {
					safeError(`[OperationCollection]`, "Scheduled sync failed", e);
				});
			}
		}, SYNC_INTERVAL_MS);
	}

	private _stopSyncInterval(): void {
		if (this.syncIntervalId) {
			clearInterval(this.syncIntervalId);
			this.syncIntervalId = null;
		}
	}

	public setAvailable(available: boolean, userId?: string) {
		this.available = available;
		if (userId) this.setUserId(userId);
		if (available) {
			this.collectorManager.startAll();
			if (this.userId) {
				this.projectSyncer.sync(this.userId).catch(e => {
					safeError(`[OperationCollection]`, "Project sync failed", e);
				});
				this._startSyncInterval();
			}
		} else {
			this.collectorManager.stopAll();
			this._stopSyncInterval();
		}
	}

	public setUserId(userId: string): void {
		this.userId = userId;
		this.cloudStorage.setUserId(userId);
	}

	public dispose(): void {
		this._commitPending();
		this._stopSyncInterval();
		this.collectorManager.dispose();
		this.cloudStorage.flush().catch(e => safeError(`[OperationCollection]`, "Flush failed", e));
	}

	public async addUserInput(userInput: string) {
		if (!userInput || userInput.trim().length === 0) {
			return;
		}

		this.addOperation(new UserRequestOperation(userInput));
	}

	public recordAcceptCompletion(params: {
		completionText: string;
		filePath: string;
		line: number;
		contextBefore: string;
		contextAfter: string;
	}): void {
		const collector = this.collectorManager.get("completion");
		if (collector instanceof CompletionCollector) {
			collector.recordAcceptCompletion(params);
		}
	}

	public triggerUploadOnCompletionRequest(): void {
		if (this.available) {
			this._commitPending();
			this.cloudStorage.triggerUploadOnCompletionRequest();
		}
	}

	get operations(): Operation[] {
		return this._operations;
	}


	private addOperation(operation: Operation) {
		if (this.pendingOperation && this.pendingOperation.tryMerge(operation)) {
			return;
		}

		this._commitPending();

		this.pendingOperation = operation;
	}

	private _commitPending(): void {
		if (this.pendingOperation) {
			this._operations.push(this.pendingOperation);
			this.cloudStorage.write(this.pendingOperation);
			this.pendingOperation = null;
		}
	}
}
