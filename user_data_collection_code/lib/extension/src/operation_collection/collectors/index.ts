
export { BaseCollector } from "./base";
export { ClipboardCollector } from "./ClipboardCollector";
export { CompletionCollector } from "./CompletionCollector";
export { DebugCollector } from "./DebugCollector";
export { EditorCollector } from "./EditorCollector";
export { FileCollector } from "./FileCollector";
export { TerminalCollector } from "./TerminalCollector";
export { TerminalExecutionCollector } from "./TerminalExecutionCollector";
export { UserRequestCollector } from "./UserRequestCollector";
export { WindowCollector } from "./WindowCollector";

import type { ICollector, ICollectorManager, IDisposable, OperationCallback } from "../types";
import { safeError, safeWarn } from "../utils/sanitize";
import { ClipboardCollector } from "./ClipboardCollector";
import { CompletionCollector } from "./CompletionCollector";
import { DebugCollector } from "./DebugCollector";
import { EditorCollector } from "./EditorCollector";
import { FileCollector } from "./FileCollector";
import { TerminalCollector } from "./TerminalCollector";
import { TerminalExecutionCollector } from "./TerminalExecutionCollector";
import { UserRequestCollector } from "./UserRequestCollector";
import { WindowCollector } from "./WindowCollector";

/**
 *
 *
 * @example
 * ```typescript
 * const manager = new CollectorManager();
 * manager.register(new EditorCollector());
 * manager.register(new FileCollector());
 *
 * manager.onOperation((op) => {
 *     console.log('Received operation:', op.operationType);
 * });
 *
 * manager.startAll();
 * manager.dispose();
 * ```
 */
export class CollectorManager implements ICollectorManager {
	private collectors: Map<string, ICollector> = new Map();
	private callbacks: Set<OperationCallback> = new Set();
	private collectorSubscriptions: Map<string, IDisposable> = new Map();

	constructor(options?: {
		enableWindowCollector?: boolean;
		enableDebugCollector?: boolean;
	}) {
		this.register(new EditorCollector());
		this.register(new FileCollector());
		this.register(new TerminalCollector());
		this.register(new TerminalExecutionCollector());
		this.register(new ClipboardCollector());
		this.register(new CompletionCollector());
		this.register(new UserRequestCollector());

		if (options?.enableWindowCollector) {
			this.register(new WindowCollector());
		}

		if (options?.enableDebugCollector !== false) {
			this.register(new DebugCollector());
		}
	}

	register(collector: ICollector): void {
		if (this.collectors.has(collector.name)) {
			safeWarn(`[CollectorManager]`, `Collector '${collector.name}' already registered, replacing...`);
			this.unregister(collector.name);
		}

		this.collectors.set(collector.name, collector);

		const subscription = collector.onOperation(op => {
			this.callbacks.forEach(cb => {
				try {
					cb(op);
				} catch (e) {
					safeError(`[CollectorManager]`, "Error in operation callback", e);
				}
			});
		});
		this.collectorSubscriptions.set(collector.name, subscription);
	}

	unregister(name: string): void {
		const collector = this.collectors.get(name);
		if (collector) {
			collector.dispose();
			this.collectors.delete(name);
		}

		const subscription = this.collectorSubscriptions.get(name);
		if (subscription) {
			subscription.dispose();
			this.collectorSubscriptions.delete(name);
		}
	}

	get(name: string): ICollector | undefined {
		return this.collectors.get(name);
	}

	getAll(): ICollector[] {
		return Array.from(this.collectors.values());
	}

	startAll(): void {
		this.collectors.forEach(collector => {
			collector.start();
		});
	}

	stopAll(): void {
		this.collectors.forEach(collector => {
			collector.stop();
		});
	}

	onOperation(callback: OperationCallback): IDisposable {
		this.callbacks.add(callback);
		return {
			dispose: () => this.callbacks.delete(callback),
		};
	}

	dispose(): void {
		this.collectors.forEach(collector => {
			collector.dispose();
		});
		this.collectors.clear();

		this.collectorSubscriptions.forEach(sub => {
			sub.dispose();
		});
		this.collectorSubscriptions.clear();

		this.callbacks.clear();
	}
}
