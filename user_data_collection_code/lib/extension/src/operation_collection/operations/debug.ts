/**
 * Debug Operations
 *
 */

import { Operation } from "./base";

export class DebugSessionStartOperation extends Operation {
	readonly role = "user";
	operationType: string = "debug_session_start";
	readableDetails: string;

	debugSessionId: string;
	debugSessionName: string;
	debugType: string;
	configuration: {
		request: string;
		name: string;
		cwd?: string;
		program?: string;
		[key: string]: any;
	};

	constructor(debugSessionId: string, debugSessionName: string, debugType: string, configuration: any) {
		super();
		this.debugSessionId = debugSessionId;
		this.debugSessionName = debugSessionName;
		this.debugType = debugType;
		this.configuration = {
			request: configuration?.request || "unknown",
			name: configuration?.name || debugSessionName,
			cwd: configuration?.cwd,
			program: configuration?.program,
		};
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		return `<note>
The user started a debug session.
</note>
<debug_session_id>${this.debugSessionId}</debug_session_id>
<debug_session_name>${this.debugSessionName}</debug_session_name>
<debug_type>${this.debugType}</debug_type>
<request>${this.configuration.request}</request>
<program>${this.configuration.program || "N/A"}</program>
<cwd>${this.configuration.cwd || "N/A"}</cwd>`;
	}
}

export class DebugSessionStopOperation extends Operation {
	readonly role = "user";
	operationType: string = "debug_session_stop";
	readableDetails: string;

	debugSessionId: string;
	debugSessionName: string;
	debugType: string;
	durationMs: number;

	constructor(debugSessionId: string, debugSessionName: string, debugType: string, durationMs: number) {
		super();
		this.debugSessionId = debugSessionId;
		this.debugSessionName = debugSessionName;
		this.debugType = debugType;
		this.durationMs = durationMs;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		return `<note>
The user ended a debug session.
</note>
<debug_session_id>${this.debugSessionId}</debug_session_id>
<debug_session_name>${this.debugSessionName}</debug_session_name>
<debug_type>${this.debugType}</debug_type>
<duration_ms>${this.durationMs}</duration_ms>`;
	}
}

export class DebugStoppedOperation extends Operation {
	readonly role = "user";
	operationType: string = "debug_stopped";
	readableDetails: string;

	debugSessionId: string;
	reason: string;
	description?: string;
	threadId?: number;
	allThreadsStopped?: boolean;
	hitBreakpointIds?: number[];

	constructor(
		debugSessionId: string,
		reason: string,
		threadId?: number,
		description?: string,
		allThreadsStopped?: boolean,
		hitBreakpointIds?: number[]
	) {
		super();
		this.debugSessionId = debugSessionId;
		this.reason = reason;
		this.threadId = threadId;
		this.description = description;
		this.allThreadsStopped = allThreadsStopped;
		this.hitBreakpointIds = hitBreakpointIds;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		const threadStr = this.threadId !== undefined ? String(this.threadId) : "unknown";
		const descStr = this.description || "";
		const breakpointStr = this.hitBreakpointIds?.join(", ") || "N/A";

		return `<note>
The debugger stopped.
</note>
<debug_session_id>${this.debugSessionId}</debug_session_id>
<reason>${this.reason}</reason>
<thread_id>${threadStr}</thread_id>
<description>${descStr}</description>
<hit_breakpoints>${breakpointStr}</hit_breakpoints>`;
	}
}

export class DebugContinuedOperation extends Operation {
	readonly role = "user";
	operationType: string = "debug_continued";
	readableDetails: string;

	debugSessionId: string;
	threadId?: number;
	allThreadsContinued?: boolean;

	constructor(debugSessionId: string, threadId?: number, allThreadsContinued?: boolean) {
		super();
		this.debugSessionId = debugSessionId;
		this.threadId = threadId;
		this.allThreadsContinued = allThreadsContinued;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		const threadStr = this.threadId !== undefined ? String(this.threadId) : "all";

		return `<note>
The debugger continued execution.
</note>
<debug_session_id>${this.debugSessionId}</debug_session_id>
<thread_id>${threadStr}</thread_id>`;
	}
}

export class DebugOutputOperation extends Operation {
	readonly role = "user";
	operationType: string = "debug_output";
	readableDetails: string;

	debugSessionId: string;
	category: string;
	output: string;
	source?: {
		name?: string;
		path?: string;
		line?: number;
		column?: number;
	};

	constructor(debugSessionId: string, category: string, output: string, source?: any) {
		super();
		this.debugSessionId = debugSessionId;
		this.category = category;
		this.output = output;
		if (source) {
			this.source = {
				name: source.name,
				path: source.path,
				line: source.line,
				column: source.column,
			};
		}
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		const sourceStr = this.source ? `${this.source.path || this.source.name || "unknown"}:${this.source.line || "?"}` : "N/A";

		return `<note>
Debug output received.
</note>
<debug_session_id>${this.debugSessionId}</debug_session_id>
<category>${this.category}</category>
<source>${sourceStr}</source>
<output>
${this.output}
</output>`;
	}

	protected mergeable(operation: Operation): boolean {
		if (!(operation instanceof DebugOutputOperation)) return false;
		if (operation.debugSessionId !== this.debugSessionId) return false;
		if (operation.category !== this.category) return false;
		if (this.output.length + operation.output.length > 10000) return false;
		return true;
	}

	protected merge(operation: Operation): void {
		const other = operation as DebugOutputOperation;
		this.output += other.output;
		this.endTimestamp = other.endTimestamp;
		this.readableDetails = this.buildReadableDetails();
	}
}

export class DebugExceptionOperation extends Operation {
	readonly role = "user";
	operationType: string = "debug_exception";
	readableDetails: string;

	debugSessionId: string;
	exceptionId: string;
	description?: string;
	breakMode?: string;
	details?: {
		message?: string;
		typeName?: string;
		stackTrace?: string;
	};

	constructor(debugSessionId: string, exceptionId: string, description?: string, breakMode?: string, details?: any) {
		super();
		this.debugSessionId = debugSessionId;
		this.exceptionId = exceptionId;
		this.description = description;
		this.breakMode = breakMode;
		if (details) {
			this.details = {
				message: details.message,
				typeName: details.typeName,
				stackTrace: details.stackTrace,
			};
		}
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		const message = this.details?.message || this.description || "unknown";
		const typeName = this.details?.typeName || this.exceptionId;

		return `<note>
An exception occurred during debugging.
</note>
<debug_session_id>${this.debugSessionId}</debug_session_id>
<exception_type>${typeName}</exception_type>
<message>${message}</message>
<break_mode>${this.breakMode || "unknown"}</break_mode>`;
	}
}
