import * as vscode from "vscode";
import { TerminalCloseOperation, TerminalOpenOperation, TerminalStateChangeOperation } from "../operations";
import { BaseCollector } from "./base";

/**
 * TerminalCollector
 *
 */
export class TerminalCollector extends BaseCollector {
	readonly name = "terminal";

	protected setupListeners(): void {
		this.disposables.push(
			vscode.window.onDidOpenTerminal(terminal => {
				this.handleTerminalOpen(terminal);
			})
		);

		this.disposables.push(
			vscode.window.onDidCloseTerminal(terminal => {
				this.handleTerminalClose(terminal);
			})
		);

		this.disposables.push(
			vscode.window.onDidChangeTerminalState(terminal => {
				this.handleTerminalStateChange(terminal);
			})
		);
	}

	private handleTerminalOpen(terminal: vscode.Terminal) {
		const operation = new TerminalOpenOperation(terminal.name, terminal.exitStatus?.code || 0);
		this.emit(operation);
	}

	private handleTerminalClose(terminal: vscode.Terminal) {
		const operation = new TerminalCloseOperation(terminal.name, terminal.exitStatus?.code || 0);
		this.emit(operation);
	}

	private handleTerminalStateChange(terminal: vscode.Terminal) {
		// Get terminal state information
		let state = "unknown";
		if (terminal.exitStatus) {
			state = `exited with code ${terminal.exitStatus.code}`;
		} else if (terminal.state) {
			state = terminal.state.toString();
		}

		const operation = new TerminalStateChangeOperation(terminal.name, terminal.exitStatus?.code || 0, state);
		this.emit(operation);
	}
}
