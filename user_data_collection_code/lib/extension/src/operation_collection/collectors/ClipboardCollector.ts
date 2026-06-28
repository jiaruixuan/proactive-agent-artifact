import * as vscode from "vscode";
import { ClipboardChangeOperation } from "../operations";
import { safeError } from "../utils/sanitize";
import { BaseCollector } from "./base";
/**
 * ClipboardCollector
 */
export class ClipboardCollector extends BaseCollector {
	readonly name = "clipboard";

	private static readonly POLLING_INTERVAL_MS = 1000;

	private lastContent: string = "";
	private pollingInterval: ReturnType<typeof setInterval> | undefined;
	private isFocused: boolean = false;

	constructor() {
		super();
		this.initializeClipboard();
	}

	protected setupListeners(): void {
		this.isFocused = vscode.window.state.focused;

		this.disposables.push(
			vscode.window.onDidChangeWindowState(state => {
				this.isFocused = state.focused;
			})
		);
	}

	private async initializeClipboard(): Promise<void> {
		try {
			this.lastContent = await vscode.env.clipboard.readText();
		} catch (error) {
			safeError(`[ClipboardCollector]`, "Failed to initialize clipboard", error);
			this.lastContent = "";
		}
	}

	override start(): void {
		super.start();
		this.startPolling();
	}

	override stop(): void {
		this.stopPolling();
		super.stop();
	}

	override dispose(): void {
		this.stopPolling();
		super.dispose();
	}

	private startPolling(): void {
		if (this.pollingInterval) return;

		this.pollingInterval = setInterval(async () => {
			if (!this.isFocused) return;

			try {
				const currentContent = await vscode.env.clipboard.readText();
				if (currentContent !== this.lastContent) {
					const previousContent = this.lastContent;
					this.lastContent = currentContent;
					this.emit(new ClipboardChangeOperation(currentContent, previousContent));
				}
			} catch (error) {
				safeError(`[ClipboardCollector]`, "Error reading clipboard", error);
			}
		}, ClipboardCollector.POLLING_INTERVAL_MS);
	}

	private stopPolling(): void {
		if (this.pollingInterval) {
			clearInterval(this.pollingInterval);
			this.pollingInterval = undefined;
		}
	}
}
