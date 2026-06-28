import * as vscode from "vscode";

import { getContext, Operation } from "./base";

export class CursorSelectionOperation extends Operation {
	readonly role = "user";
	operationType: string = "cursor_selection";
	readableDetails: string;
	uri: string;
	anchorOffset: number;
	activeOffset: number;
	document: vscode.TextDocument;

	constructor(uri: string, anchorOffset: number, activeOffset: number, document: vscode.TextDocument) {
		super();
		this.uri = uri;
		this.anchorOffset = anchorOffset;
		this.activeOffset = activeOffset;
		this.document = document;
		this.readableDetails = this.buildReadableDetails();
	}

	protected override mergeable(operation: Operation): boolean {
		if (operation instanceof CursorSelectionOperation && operation.uri === this.uri) {
			return true;
		}
		if (operation instanceof ChangeViewOperation && operation.uri === this.uri) {
			return true;
		}
		return false;
	}

	protected override merge(operation: Operation): void {
		if (operation instanceof CursorSelectionOperation) {
			this.endTimestamp = operation.endTimestamp;
			this.anchorOffset = operation.anchorOffset;
			this.activeOffset = operation.activeOffset;
			this.document = operation.document;
			this.readableDetails = this.buildReadableDetails();
		}
	}

	buildReadableDetails(): string {
		const uri = this.uri;
		const anchorOffset = this.anchorOffset;
		const activeOffset = this.activeOffset;
		const document = this.document;

		const leftOffset = Math.min(anchorOffset, activeOffset);
		const rightOffset = Math.max(anchorOffset, activeOffset);
		const [startOffset, endOffset] = getContext(document, leftOffset, rightOffset);

		const startPos = document.positionAt(startOffset);
		const endPos = document.positionAt(endOffset);
		const leftPos = document.positionAt(leftOffset);
		const rightPos = document.positionAt(rightOffset);
		const prefix = document.getText(new vscode.Range(startPos, leftPos));
		const middle = document.getText(new vscode.Range(leftPos, rightPos));
		const suffix = document.getText(new vscode.Range(rightPos, endPos));
		let result = "";
		if (anchorOffset === activeOffset) {
			const context = prefix + "<FT_cursor/>" + suffix;
			result = `<path>${uri}</path>
<cursor_position>
<line>${leftPos.line + 1}</line>
<column>${leftPos.character + 1}</column>
</cursor_position>
<context_after_operation>
<note>
The following is the text adjacent to the operated text after this operation, corresponding to lines ${startPos.line + 1}-${endPos.line + 1} in the file. <FT_cursor/> indicates the position of the cursor after this operation.
</note>
<context_start_line>${startPos.line + 1}</context_start_line>
<context_end_line>${endPos.line + 1}</context_end_line>
<context_content>
${context}
</context_content>
</context_after_operation>`;
		} else {
			const context = prefix + "<FT_cursor_selection>" + middle + "</FT_cursor_selection>" + suffix;
			result = `<path>${uri}</path>
<selection_start_position>
<line>${leftPos.line + 1}</line>
<column>${leftPos.character + 1}</column>
</selection_start_position>
<selection_end_position>
<line>${rightPos.line + 1}</line>
<column>${rightPos.character + 1}</column>
</selection_end_position>
<context_after_operation>
<note>
The following is the text adjacent to the operated text after this operation, corresponding to lines ${startPos.line + 1}-${endPos.line + 1} in the file. The part wrapped with <FT_cursor_selection></FT_cursor_selection> indicates the text selected in this operation.
</note>
<context_start_line>${startPos.line + 1}</context_start_line>
<context_end_line>${endPos.line + 1}</context_end_line>
<context_content>
${context}
</context_content>
</context_after_operation>`;
		}
		return result;
	}
}

export class EditTextOperation extends Operation {
	readonly role = "user";
	operationType: string = "edit_text";
	readableDetails: string;
	uri: string;
	oldContent: string;
	newContent: string;
	samePrefixLength: number;
	sameSuffixLength: number;
	document: vscode.TextDocument;

	private static readonly IS_NEAR_CHARS = 10;

	constructor(uri: string, oldContent: string, newContent: string, document: vscode.TextDocument) {
		super();
		this.uri = uri;
		this.oldContent = oldContent;
		this.newContent = newContent;
		this.document = document;

		this.samePrefixLength = 0;
		this.sameSuffixLength = 0;
		for (let i = 0; i < Math.min(oldContent.length, newContent.length); i++) {
			if (oldContent[i] === newContent[i]) {
				this.samePrefixLength += 1;
			} else {
				break;
			}
		}
		for (let i = 0; i < Math.min(oldContent.length, newContent.length); i++) {
			if (oldContent[oldContent.length - 1 - i] === newContent[newContent.length - 1 - i]) {
				this.sameSuffixLength += 1;
			} else {
				break;
			}
		}
		this.sameSuffixLength = Math.min(
			this.sameSuffixLength,
			Math.min(oldContent.length, newContent.length) - this.samePrefixLength
		);

		this.readableDetails = this.buildReadableDetails();
	}

	protected override mergeable(operation: Operation): boolean {
		if (operation instanceof EditTextOperation) {
			if (operation.uri !== this.uri || this.newContent !== operation.oldContent) {
				return false;
			}
			const thisStart = this.samePrefixLength;
			const thisEnd = this.newContent.length - this.sameSuffixLength;
			const opStart = operation.samePrefixLength;
			const opEnd = operation.newContent.length - operation.sameSuffixLength;
			if (!(thisEnd < opStart - EditTextOperation.IS_NEAR_CHARS || thisStart > opEnd + EditTextOperation.IS_NEAR_CHARS)) {
				return true;
			}
			return false;
		}
		if (operation instanceof ChangeViewOperation && operation.uri === this.uri) {
			return true;
		}
		return false;
	}

	protected override merge(operation: Operation): void {
		if (operation instanceof EditTextOperation) {
			this.endTimestamp = operation.endTimestamp;
			this.newContent = operation.newContent;
			this.samePrefixLength = Math.min(this.samePrefixLength, operation.samePrefixLength);
			this.sameSuffixLength = Math.min(this.sameSuffixLength, operation.sameSuffixLength);
			this.document = operation.document;
			this.readableDetails = this.buildReadableDetails();
		}
	}

	buildReadableDetails(): string {
		const uri = this.uri;
		const offset = this.samePrefixLength;
		const deletedContent = this.oldContent.substring(this.samePrefixLength, this.oldContent.length - this.sameSuffixLength);
		const newContent = this.newContent.substring(this.samePrefixLength, this.newContent.length - this.sameSuffixLength);
		const document = this.document;
		const [startOffset, endOffset] = getContext(document, offset, offset + newContent.length);

		const startPos = document.positionAt(startOffset);
		const endPos = document.positionAt(endOffset);
		const insertStartPos = document.positionAt(offset);
		const insertEndPos = document.positionAt(offset + newContent.length);
		const prefix = document.getText(new vscode.Range(startPos, insertStartPos));
		const middle = document.getText(new vscode.Range(insertStartPos, insertEndPos));
		const suffix = document.getText(new vscode.Range(insertEndPos, endPos));
		let context = prefix;
		if (deletedContent.length > 0) {
			context += "<FT_delete>" + deletedContent + "</FT_delete>";
		}
		if (newContent.length > 0) {
			context += "<FT_insert>" + middle + "</FT_insert>";
		}
		context += suffix;
		const result = `<path>${uri}</path>
<context_after_operation>
<note>
The following is the text adjacent to the operated text after this operation, corresponding to lines ${startPos.line + 1}-${endPos.line + 1} in the file. The part wrapped with <FT_delete></FT_delete> is replaced by the content wrapped with <FT_insert></FT_insert> in this operation.
</note>
<context_start_line>${startPos.line + 1}</context_start_line>
<context_end_line>${endPos.line + 1}</context_end_line>
<context_content>
${context}
</context_content>
</context_after_operation>`;
		return result;
	}
}

export class ChangeViewOperation extends Operation {
	readonly role = "user";
	operationType: string = "change_view";
	readableDetails: string;
	uri: string;
	viewStart: number;
	viewEnd: number;
	document: vscode.TextDocument;

	constructor(uri: string, viewStart: number, viewEnd: number, document: vscode.TextDocument) {
		super();
		this.uri = uri;
		this.viewStart = viewStart;
		this.viewEnd = viewEnd;
		this.document = document;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		const uri = this.uri;
		const document = this.document;
		const viewStart = this.viewStart;
		const viewEnd = this.viewEnd;

		const viewStartPos = new vscode.Position(Math.max(0, viewStart), 0);
		const viewEndPos = new vscode.Position(
			Math.min(document.lineCount - 1, viewEnd),
			document.lineAt(Math.min(document.lineCount - 1, viewEnd)).text.length
		);

		const viewContent = document.getText(new vscode.Range(viewStartPos, viewEndPos));
		const result = `<path>${uri}</path>
<current_view>
<start_line>${viewStart + 1}</start_line>
<end_line>${viewEnd + 1}</end_line>
</current_view>
<context_after_operation>
<note>
The following is the text visible in the viewport after this view change operation, corresponding to lines ${viewStart + 1}-${viewEnd + 1} in the file.
</note>
<visible_start_line>${viewStart + 1}</visible_start_line>
<visible_end_line>${viewEnd + 1}</visible_end_line>
<visible_content>
${viewContent}
</visible_content>
</context_after_operation>`;
		return result;
	}

	protected mergeable(operation: Operation): boolean {
		return operation instanceof ChangeViewOperation && operation.uri === this.uri;
	}
	protected merge(operation: Operation): void {
		if (operation instanceof ChangeViewOperation) {
			this.endTimestamp = operation.endTimestamp;
			this.viewStart = operation.viewStart;
			this.viewEnd = operation.viewEnd;
			this.document = operation.document;
			this.readableDetails = this.buildReadableDetails();
		}
	}
}
