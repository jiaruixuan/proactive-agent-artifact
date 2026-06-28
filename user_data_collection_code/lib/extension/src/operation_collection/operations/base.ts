import * as vscode from "vscode";

const SINGLE_OPERATION_MAX_LENGTH = 3000;
const SINGLE_LINE_MAX_CHARS = 300;
const CONTEXT_MAX_LINES = 5;
const CONTEXT_MAX_CHARS = 500;

let operationIdCounter = 50000;

export function getContext(document: vscode.TextDocument, start: number, end: number): [number, number] {
	const startLine = document.positionAt(start).line;
	const startCharacter = document.positionAt(start).character;
	const endLine = document.positionAt(end).line;
	const endCharacter = document.positionAt(end).character;
	let startOffset = 0;
	let endOffset = 0;
	// process prefix
	if (startCharacter > CONTEXT_MAX_CHARS) {
		startOffset = start - CONTEXT_MAX_CHARS;
	} else {
		startOffset = document.offsetAt(new vscode.Position(startLine, 0));
		for (let i = startLine - 1; i >= Math.max(0, startLine - CONTEXT_MAX_LINES); i--) {
			const temp = document.offsetAt(new vscode.Position(i, 0));
			if (start - temp <= CONTEXT_MAX_CHARS) {
				startOffset = temp;
			} else {
				break;
			}
		}
	}
	// process suffix
	if (document.lineAt(endLine).text.length - endCharacter > CONTEXT_MAX_CHARS) {
		endOffset = end + CONTEXT_MAX_CHARS;
	} else {
		endOffset = document.offsetAt(new vscode.Position(endLine, document.lineAt(endLine).text.length));
		for (let i = endLine + 1; i <= Math.min(document.lineCount - 1, endLine + CONTEXT_MAX_LINES); i++) {
			const temp = document.offsetAt(new vscode.Position(i, document.lineAt(i).text.length));
			if (temp - end <= CONTEXT_MAX_CHARS) {
				endOffset = temp;
			} else {
				break;
			}
		}
	}
	return [startOffset, endOffset];
}

function getNewline(content: string): string {
	let newline = "\n";
	if (content.includes("\r\n")) {
		newline = "\r\n";
	} else if (content.includes("\r")) {
		newline = "\r";
	}
	return newline;
}

export function getLimitedContext(text: string): string {
	if (text.length <= SINGLE_OPERATION_MAX_LENGTH) {
		return text;
	}
	const newline = getNewline(text);
	const lines = text.split(newline);
	let prefix = "";
	let suffix = "";
	let prefixIdx = 0;
	let suffixIdx = lines.length - 1;
	while (prefixIdx <= suffixIdx) {
		let modified = false;
		// Process from prefix
		let line = lines[prefixIdx]!;
		if (line.length <= SINGLE_LINE_MAX_CHARS && (prefix + line + newline + suffix).length <= SINGLE_OPERATION_MAX_LENGTH - 50) {
			prefix += line + newline;
			prefixIdx++;
			modified = true;
		}
		// Process from suffix
		line = lines[suffixIdx]!;
		if (line.length <= SINGLE_LINE_MAX_CHARS && (prefix + newline + line + suffix).length <= SINGLE_OPERATION_MAX_LENGTH - 50) {
			suffix = newline + line + suffix;
			suffixIdx--;
			modified = true;
		}
		if (!modified) {
			break;
		}
	}
	return prefix + "...<FT_omitted_lines>" + suffix;
}

export abstract class Operation {
	//     question: string
	//     readableContext: string // question

	abstract role: string;
	abstract readableDetails: string;
	abstract operationType: string;
	id: number;
	startTimestamp: number;
	endTimestamp: number;

	protected mergeable(_operation: Operation): boolean {
		return false;
	}

	protected merge(_operation: Operation): void {
	}

	constructor() {
		this.id = operationIdCounter++;
		const now = new Date().getTime();
		this.startTimestamp = now;
		this.endTimestamp = now;
	}

	tryMerge(operation: Operation): boolean {
		if (this.mergeable(operation)) {
			this.merge(operation);
			return true;
		} else {
			return false;
		}
	}

	formatTimestamp(ts: number): string {
		return new Date(ts).toLocaleString();
	}

	toString(): string {
		let result = `
<${this.operationType}_operation>
<operating_role> ${this.role} </operating_role>
<operation_time> ${this.formatTimestamp(this.startTimestamp)} - ${this.formatTimestamp(this.endTimestamp)} </operation_time>
<details>
${this.readableDetails}
</details>
</${this.operationType}_operation>`;
		if (this.operationType !== "user_request") {
			result = getLimitedContext(result);
		}
		return result;
	}
}
