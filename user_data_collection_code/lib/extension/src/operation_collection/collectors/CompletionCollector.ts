
import { AcceptCompletionOperation } from "../operations/completion";
import { BaseCollector } from "./base";

export class CompletionCollector extends BaseCollector {
	readonly name = "completion";

	private static instance: CompletionCollector | null = null;

	static getInstance(): CompletionCollector | null {
		return CompletionCollector.instance;
	}

	protected setupListeners(): void {
		CompletionCollector.instance = this;
	}

	public recordAcceptCompletion(params: {
		completionText: string;
		filePath: string;
		line: number;
		contextBefore: string;
		contextAfter: string;
	}): void {
		if (!this._isActive) {
			return;
		}

		const operation = new AcceptCompletionOperation(params);

		this.emit(operation);
	}

	dispose(): void {
		super.dispose();
		CompletionCollector.instance = null;
	}
}
