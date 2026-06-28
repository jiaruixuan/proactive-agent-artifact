
import type * as vscode from "vscode";
import type { ICollector, IDisposable, IOperation, OperationCallback } from "../types";
import { safeError } from "../utils/sanitize";

/**
 *
 *
 * @example
 * ```typescript
 * class EditorCollector extends BaseCollector {
 *     readonly name = 'editor';
 *
 *     protected setupListeners(): void {
 *         this.disposables.push(
 *             vscode.workspace.onDidChangeTextDocument((e) => {
 *                 const op = new EditTextOperation(...);
 *                 this.emit(op);
 *             })
 *         );
 *     }
 * }
 * ```
 */
export abstract class BaseCollector implements ICollector {
	abstract readonly name: string;

	protected _isActive = false;

	protected disposables: vscode.Disposable[] = [];

	protected callbacks: Set<OperationCallback> = new Set();

	get isActive(): boolean {
		return this._isActive;
	}

	protected abstract setupListeners(): void;

	protected emit(operation: IOperation): void {
		this.callbacks.forEach(cb => {
			try {
				cb(operation);
			} catch (e) {
				safeError(`[${this.name}]`, "Error in operation callback", e);
			}
		});
	}

	onOperation(callback: OperationCallback): IDisposable {
		this.callbacks.add(callback);
		return {
			dispose: () => this.callbacks.delete(callback),
		};
	}

	start(): void {
		if (this._isActive) {
			return;
		}
		this._isActive = true;
		this.setupListeners();
	}

	stop(): void {
		if (!this._isActive) {
			return;
		}
		this._isActive = false;
		this.disposables.forEach(d => d.dispose());
		this.disposables = [];
	}

	dispose(): void {
		this.stop();
		this.callbacks.clear();
	}
}
