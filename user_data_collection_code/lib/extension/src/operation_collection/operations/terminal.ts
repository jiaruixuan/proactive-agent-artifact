import { assert } from "console";

import { Operation } from "./base";

export class TerminalOpenOperation extends Operation {
	readonly role = "user";
	MERGE_TIME_GAP = 0; // Merge terminal operations within 5 seconds
	operationType: string = "terminal_open";
	readableDetails: string;
	terminalName: string;
	terminalId: number;

	constructor(terminalName: string, terminalId: number) {
		super();
		this.terminalName = terminalName;
		this.terminalId = terminalId;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		const result = `<note>
The user opened a new terminal.
</note>
<terminal_name>${this.terminalName}</terminal_name>
<terminal_id>${this.terminalId}</terminal_id>`;
		return result;
	}

	protected mergeable(operation: Operation): boolean {
		return false;
	}

	protected merge(operation: Operation): void {
		assert(false);
	}
}

export class TerminalCloseOperation extends Operation {
	readonly role = "user";
	MERGE_TIME_GAP = 0; // Merge terminal operations within 5 seconds
	operationType: string = "terminal_close";
	readableDetails: string;
	terminalName: string;
	terminalId: number;

	constructor(terminalName: string, terminalId: number) {
		super();
		this.terminalName = terminalName;
		this.terminalId = terminalId;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		const result = `<note>
The user closed a terminal.
</note>
<terminal_name>${this.terminalName}</terminal_name>
<terminal_id>${this.terminalId}</terminal_id>`;
		return result;
	}

	protected mergeable(operation: Operation): boolean {
		return false;
	}

	protected merge(operation: Operation): void {
		assert(false);
	}
}

export class TerminalStateChangeOperation extends Operation {
	readonly role = "user";
	MERGE_TIME_GAP = 0; // Merge terminal state changes within 10 seconds
	operationType: string = "terminal_state_change";
	readableDetails: string;
	terminalName: string;
	terminalId: number;
	state: string;

	constructor(terminalName: string, terminalId: number, state: string) {
		super();
		this.terminalName = terminalName;
		this.terminalId = terminalId;
		this.state = state;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		const result = `<note>
The user changed terminal state.
</note>
<terminal_name>${this.terminalName}</terminal_name>
<terminal_id>${this.terminalId}</terminal_id>
<state>${this.state}</state>`;
		return result;
	}

	protected mergeable(operation: Operation): boolean {
		return false;
	}

	protected merge(operation: Operation): void {
		assert(false);
	}
}

export class TerminalExecutionOperation extends Operation {
	readonly role = "user";
	MERGE_TIME_GAP = 0;
	operationType: string = "terminal_execution";
	readableDetails: string;

	terminalName: string;
	terminalId?: number;
	commandLine: string;
	output: string;
	exitCode?: number;
	durationMs: number;

	constructor(
		terminalName: string,
		terminalId: number | undefined,
		commandLine: string,
		output: string,
		exitCode: number | undefined,
		durationMs: number
	) {
		super();
		this.terminalName = terminalName;
		this.terminalId = terminalId;
		this.commandLine = commandLine;
		this.output = output;
		this.exitCode = exitCode;
		this.durationMs = durationMs;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		const exitCodeStr = this.exitCode === undefined ? "unknown" : String(this.exitCode);
		const terminalIdStr = this.terminalId === undefined ? "unknown" : String(this.terminalId);

		return `<note>
The user executed a command in terminal.
</note>
<terminal_name>${this.terminalName}</terminal_name>
<terminal_id>${terminalIdStr}</terminal_id>
<command_line>${this.commandLine}</command_line>
<exit_code>${exitCodeStr}</exit_code>
<duration_ms>${this.durationMs}</duration_ms>
<output>
${this.output}
</output>`;
	}

	protected mergeable(operation: Operation): boolean {
		return false;
	}

	protected merge(operation: Operation): void {
		assert(false);
	}
}
