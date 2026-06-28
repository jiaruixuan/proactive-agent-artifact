import * as vscode from "vscode";
import { WindowStateChangeOperation } from "../operations";
import { BaseCollector } from "./base";

/**
 * WindowCollector
 *
 *
 */
export class WindowCollector extends BaseCollector {
	readonly name = "window";

	protected setupListeners(): void {
		this.disposables.push(
			vscode.window.onDidChangeWindowState(event => {
				const operation = new WindowStateChangeOperation(event.focused, true);
				this.emit(operation);
			})
		);
	}
}
