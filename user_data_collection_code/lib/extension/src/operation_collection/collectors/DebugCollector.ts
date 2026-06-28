/**
 * DebugCollector
 * {
    "type": "event",
    "event": "stopped",
    "body": {
        "reason": "breakpoint",
        "threadId": 1,
        "hitBreakpointIds": [1]
    }
}
 
 * 
*/

import * as vscode from "vscode";
import {
	DebugContinuedOperation,
	DebugExceptionOperation,
	DebugOutputOperation,
	DebugSessionStartOperation,
	DebugSessionStopOperation,
	DebugStoppedOperation,
} from "../operations";
import { safeError } from "../utils/sanitize";
import { BaseCollector } from "./base";

interface DebugSessionState {
	session: vscode.DebugSession;
	startTime: number;
	lastOutputOp?: DebugOutputOperation;
}

export class DebugCollector extends BaseCollector {
	readonly name = "debug";
	private sessions = new Map<string, DebugSessionState>();

	protected setupListeners(): void {
		this.disposables.push(
			vscode.debug.registerDebugAdapterTrackerFactory("*", {
				createDebugAdapterTracker: session => this.createTracker(session),
			})
		);

		this.disposables.push(vscode.debug.onDidStartDebugSession(session => this.onSessionStart(session)));

		this.disposables.push(vscode.debug.onDidTerminateDebugSession(session => this.onSessionStop(session)));
	}

	private createTracker(session: vscode.DebugSession): vscode.DebugAdapterTracker {
		const sessionId = session.id;
		return {
			onDidSendMessage: (msg: any) => {
				if (msg.type === "event") {
					this.parseHighLevelEvent(sessionId, msg);
				}
			},
			onError: (error: Error) => {
				safeError(`[DebugCollector]`, `Session ${sessionId} error`, error.message);
			},
		};
	}

	private onSessionStart(session: vscode.DebugSession): void {
		this.sessions.set(session.id, {
			session,
			startTime: Date.now(),
		});
		this.emit(new DebugSessionStartOperation(session.id, session.name, session.type, session.configuration));
	}

	private onSessionStop(session: vscode.DebugSession): void {
		const state = this.sessions.get(session.id);
		if (state?.lastOutputOp) {
			this.emit(state.lastOutputOp);
		}
		const durationMs = state ? Date.now() - state.startTime : 0;
		this.emit(new DebugSessionStopOperation(session.id, session.name, session.type, durationMs));
		this.sessions.delete(session.id);
	}

	private parseHighLevelEvent(sessionId: string, msg: any): void {
		const body = msg.body || {};
		switch (msg.event) {
			case "stopped":
				this.onStoppedEvent(sessionId, body);
				break;
			case "continued":
				this.emit(new DebugContinuedOperation(sessionId, body.threadId, body.allThreadsContinued));
				break;
			case "output":
				this.onOutputEvent(sessionId, body);
				break;
		}
	}

	private onStoppedEvent(sessionId: string, body: any): void {
		this.emit(
			new DebugStoppedOperation(
				sessionId,
				body.reason || "unknown",
				body.threadId,
				body.description,
				body.allThreadsStopped,
				body.hitBreakpointIds
			)
		);
		if (body.reason === "exception" && body.text) {
			this.emit(new DebugExceptionOperation(sessionId, body.text, body.description, undefined, undefined));
		}
	}

	private onOutputEvent(sessionId: string, body: any): void {
		const state = this.sessions.get(sessionId);
		if (!state) return;

		const output = body.output || "";
		if (!output.trim()) return;

		const newOp = new DebugOutputOperation(sessionId, body.category || "console", output, body.source);

		if (state.lastOutputOp?.tryMerge(newOp)) {
			return;
		}
		if (state.lastOutputOp) {
			this.emit(state.lastOutputOp);
		}
		state.lastOutputOp = newOp;
	}

	private flushPendingOutputs(): void {
		this.sessions.forEach(state => {
			if (state.lastOutputOp) {
				this.emit(state.lastOutputOp);
				state.lastOutputOp = undefined;
			}
		});
	}

	override stop(): void {
		this.flushPendingOutputs();
		super.stop();
	}
}
