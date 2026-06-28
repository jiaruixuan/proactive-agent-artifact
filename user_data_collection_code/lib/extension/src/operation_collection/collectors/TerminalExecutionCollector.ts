import * as vscode from "vscode";
import { TerminalExecutionOperation } from "../operations";
import { BaseCollector } from "./base";

/**
 * TerminalExecutionCollector

 */
export class TerminalExecutionCollector extends BaseCollector {
	readonly name = "terminal_execution";

	private readonly executions = new WeakMap<object, ExecutionState>();

	override start(): void {
		const winAny = vscode.window as any;
		if (
			typeof winAny?.onDidStartTerminalShellExecution !== "function" ||
			typeof winAny?.onDidEndTerminalShellExecution !== "function"
		) {
			return;
		}
		super.start();
	}

	protected setupListeners(): void {
		const winAny = vscode.window as any;
		const onStart = winAny?.onDidStartTerminalShellExecution;
		const onEnd = winAny?.onDidEndTerminalShellExecution;

		if (typeof onStart !== "function" || typeof onEnd !== "function") {
			return;
		}

		this.disposables.push(
			onStart((event: any) => {
				void this.handleStart(event);
			})
		);

		this.disposables.push(
			onEnd((event: any) => {
				void this.handleEnd(event);
			})
		);
	}

	private async handleStart(event: any): Promise<void> {
		const startTs = Date.now();
		const terminal = event?.terminal as vscode.Terminal | undefined;
		const execution = event?.execution as any;

		if (!execution || typeof execution !== "object") {
			return;
		}

		const commandLineValue = this.getCommandLineValue(execution?.commandLine);
		const startTime = Date.now();

		const state: ExecutionState = {
			terminalName: terminal?.name ?? "unknown",
			terminalId: undefined,
			commandLine: commandLineValue,
			startTime,
			outputChunks: [],
			outputChars: 0,
			truncated: false,
			readPromise: undefined,
		};

		this.executions.set(execution as object, state);

		if (terminal) {
			Promise.resolve(terminal.processId)
				.then(pid => {
					if (typeof pid === "number") {
						state.terminalId = pid;
					}
				})
				.catch(() => {
					// ignore
				});
		}

		state.readPromise = this.readOutput(execution, state);
	}

	private async handleEnd(event: any): Promise<void> {
		const execution = event?.execution as any;
		if (!execution || typeof execution !== "object") {
			return;
		}

		const state = this.executions.get(execution as object);
		if (!state) {
			return;
		}

		if (state.readPromise) {
			try {
				await Promise.race([state.readPromise, delayMs(500)]);
			} catch {
				// ignore
			}
		}

		const endTime = Date.now();
		const exitCode = typeof event?.exitCode === "number" ? (event.exitCode as number) : undefined;
		const durationMs = endTime - state.startTime;

		let output = state.outputChunks.join("");
		output = cleanTerminalOutput(output);
		if (state.truncated) {
			output += "\n...(output truncated)";
		}

		const op = new TerminalExecutionOperation(
			state.terminalName,
			state.terminalId,
			state.commandLine,
			output,
			exitCode,
			durationMs
		);
		op.startTimestamp = state.startTime;
		op.endTimestamp = endTime;

		this.emit(op);
		this.executions.delete(execution as object);
	}

	private async readOutput(execution: any, state: ExecutionState): Promise<void> {
		const readFn = execution?.read;

		if (typeof readFn !== "function") {
			return;
		}

		let stream: AsyncIterable<any>;
		try {
			stream = execution.read();
		} catch (e: any) {
			return;
		}

		let chunkCount = 0;
		let skippedNonString = 0;
		let skippedNoContent = 0;

		try {
			for await (const chunk of stream) {
				chunkCount++;

				if (typeof chunk !== "string") {
					skippedNonString++;
					continue;
				}

				const cleaned = cleanTerminalOutput(chunk);
				const contentCheck = hasAnyContent(cleaned);

				if (!contentCheck) {
					skippedNoContent++;
					continue;
				}

				state.outputChunks.push(cleaned);
				state.outputChars += cleaned.length;

				if (state.outputChars >= MAX_OUTPUT_CHARS) {
					state.truncated = true;
					break;
				}
			}
		} catch (e: any) {}
	}

	private getCommandLineValue(commandLine: any): string {
		if (typeof commandLine === "string") {
			return commandLine;
		}
		if (commandLine && typeof commandLine.value === "string") {
			return commandLine.value;
		}
		return "";
	}
}

const MAX_OUTPUT_CHARS = 30_000;

interface ExecutionState {
	terminalName: string;
	terminalId?: number;
	commandLine: string;
	startTime: number;
	outputChunks: string[];
	outputChars: number;
	truncated: boolean;
	readPromise?: Promise<void>;
}

function hasAnyContent(text: string): boolean {
	return text.split("\n").some(line => line.trim().length > 0);
}

function delayMs(ms: number): Promise<void> {
	return new Promise(resolve => setTimeout(resolve, ms));
}

function cleanTerminalOutput(data: string): string {
	let cleaned = data;

	// VS Code shell integration sequences (OSC 633)
	cleaned = cleaned.replace(/\x1b\]633;[^\x07]*\x07/g, "");
	cleaned = cleaned.replace(/\x1b\]633;[^\x1b]*\x1b\\/g, "");

	// All OSC sequences
	cleaned = cleaned.replace(/\x1b\][^\x07\x1b]*(\x07|\x1b\\)/g, "");

	// All CSI sequences
	cleaned = cleaned.replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, "");

	// Character set sequences
	cleaned = cleaned.replace(/\x1b[()][AB012]/g, "");

	// Other escape sequences
	cleaned = cleaned.replace(/\x1b[=>]/g, "");
	cleaned = cleaned.replace(/\x1b[78]/g, "");

	// Bell
	cleaned = cleaned.replace(/\x07/g, "");

	// carriage return overwrite behavior
	cleaned = cleaned.replace(/\r(?!\n)/g, "");

	// remove zsh prompt artifacts
	const lines = cleaned.split("\n");
	const filteredLines = lines.filter(line => {
		const trimmed = line.trim();
		return !(trimmed === "%" || trimmed === "%\r");
	});
	cleaned = filteredLines.join("\n");

	// collapse many newlines
	cleaned = cleaned.replace(/\n{3,}/g, "\n\n");

	return cleaned;
}
