import { getLimitedContext, Operation } from "./base";

export class ClipboardChangeOperation extends Operation {
	readonly role = "user";
	operationType: string = "clipboard_change";
	readableDetails: string;
	clipboardContent: string;
	previousContent: string;

	constructor(clipboardContent: string, previousContent: string = "") {
		super();
		this.clipboardContent = clipboardContent;
		this.previousContent = previousContent;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		const limitedContent = getLimitedContext(this.clipboardContent);
		return `<note>
The user copied text to the clipboard.
</note>
<clipboard_content>
${limitedContent}
</clipboard_content>`;
	}

	protected mergeable(operation: Operation): boolean {
		return operation instanceof ClipboardChangeOperation;
	}

	protected merge(operation: Operation): void {
		if (operation instanceof ClipboardChangeOperation) {
			this.endTimestamp = operation.endTimestamp;
			this.previousContent = this.clipboardContent;
			this.clipboardContent = operation.clipboardContent;
			this.readableDetails = this.buildReadableDetails();
		}
	}
}

export class WindowStateChangeOperation extends Operation {
	readonly role = "user";
	operationType: string = "window_state_change";
	readableDetails: string;
	focused: boolean;
	visible: boolean;

	constructor(focused: boolean, visible: boolean = true) {
		super();
		this.focused = focused;
		this.visible = visible;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		let result = `<note>
The user changed window state.
</note>
<window_focused>${this.focused}</window_focused>`;
		if (this.visible !== undefined) {
			result += `\n<window_visible>${this.visible}</window_visible>`;
		}
		return result;
	}

	protected mergeable(operation: Operation): boolean {
		return (
			operation instanceof WindowStateChangeOperation && operation.focused === this.focused && operation.visible === this.visible
		);
	}

	protected merge(operation: Operation): void {
		if (operation instanceof WindowStateChangeOperation) {
			this.endTimestamp = operation.endTimestamp;
			this.readableDetails = this.buildReadableDetails();
		}
	}
}
