
import { Operation } from "./base";

export class AcceptCompletionOperation extends Operation {
	readonly role = "user";

	readonly operationType = "accept_completion";

	readableDetails: string;

	completionText: string;

	filePath: string;

	line: number;

	contextBefore: string;

	contextAfter: string;

	constructor(params: {
		completionText: string;
		filePath: string;
		line: number;
		contextBefore: string;
		contextAfter: string;
	}) {
		super();
		this.completionText = params.completionText;
		this.filePath = params.filePath;
		this.line = params.line;
		this.contextBefore = params.contextBefore;
		this.contextAfter = params.contextAfter;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		return `<note>
The user accepted an inline code completion suggestion (similar to Cursor Tab).
</note>
<file_path>${this.filePath}</file_path>
<line>${this.line + 1}</line>
<context_before>
${this.contextBefore}
</context_before>
<completion_text>
${this.completionText}
</completion_text>
<context_after>
${this.contextAfter}
</context_after>`;
	}
}
