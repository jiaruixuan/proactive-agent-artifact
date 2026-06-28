import * as vscode from "vscode";
import { ChangeViewOperation, CursorSelectionOperation, EditTextOperation } from "../operations";
import { BaseCollector } from "./base";

/**
 * EditorCollector
 *
 */
export class EditorCollector extends BaseCollector {
	readonly name = "editor";

	private documentStates = new Map<string, string>();
	private viewStates = new Map<string, [number, number]>();

	protected setupListeners(): void {
		this.initializeOpenDocuments();

		this.disposables.push(
			vscode.workspace.onDidOpenTextDocument(document => {
				this.saveDocumentState(document);
			})
		);

		this.disposables.push(
			vscode.workspace.onDidChangeTextDocument(event => {
				this.handleDocumentChange(event);
			})
		);

		this.disposables.push(
			vscode.window.onDidChangeTextEditorVisibleRanges(event => {
				this.handleViewChange(event.textEditor);
			})
		);

		this.disposables.push(
			vscode.window.onDidChangeActiveTextEditor(editor => {
				if (editor) {
					this.handleViewChange(editor);
				}
			})
		);

		this.disposables.push(
			vscode.window.onDidChangeTextEditorSelection(event => {
				this.handleSelectionChange(event);
			})
		);
	}

	private initializeOpenDocuments() {
		vscode.workspace.textDocuments.forEach(document => {
			if (document.uri.scheme === "file" || document.uri.scheme === "untitled") {
				this.saveDocumentState(document);
			}
		});
	}

	private saveDocumentState(document: vscode.TextDocument) {
		if (document.uri.scheme === "file" || document.uri.scheme === "untitled") {
			this.documentStates.set(document.uri.toString(), document.getText());
		}
	}

	private saveViewState(uri: string, range: [number, number]) {
		this.viewStates.set(uri, range);
	}

	private handleSelectionChange(event: vscode.TextEditorSelectionChangeEvent) {
		if (event.textEditor.document.uri.scheme !== "file" && event.textEditor.document.uri.scheme !== "untitled") {
			return;
		}
		if (event.selections.length > 0) {
			const document = event.textEditor.document;
			const anchorOffset = document.offsetAt(event.selections[0]!.anchor);
			const activeOffset = document.offsetAt(event.selections[0]!.active);
			const operation = new CursorSelectionOperation(document.uri.toString(), anchorOffset, activeOffset, document);
			this.emit(operation);
		}
	}

	private handleDocumentChange(event: vscode.TextDocumentChangeEvent) {
		if (event.document.uri.scheme !== "file" && event.document.uri.scheme !== "untitled") {
			return;
		}
		if (event.document.uri.path.includes(".ideassistant/ada_context.json")) {
			return;
		}
		const uri = event.document.uri.toString();
		const previousContent = this.documentStates.get(uri);
		if (previousContent === undefined) {
			this.saveDocumentState(event.document);
			return;
		}
		if (event.contentChanges.length > 0) {
			const newContent = event.document.getText();
			const operation = new EditTextOperation(uri, previousContent, newContent, event.document);
			this.emit(operation);
		}
		this.saveDocumentState(event.document);
	}

	private handleViewChange(editor: vscode.TextEditor) {
		if (editor.document.uri.scheme !== "file" && editor.document.uri.scheme !== "untitled") {
			return;
		}
		if (editor.visibleRanges.length > 0) {
			const firstRange = editor.visibleRanges[0];
			const lastRange = editor.visibleRanges[editor.visibleRanges.length - 1];

			const viewStart = firstRange!.start.line;
			const viewEnd = lastRange!.end.line;

			const uri = editor.document.uri.toString();

			const operation = new ChangeViewOperation(uri, viewStart, viewEnd, editor.document);

			this.emit(operation);
			this.saveViewState(uri, [viewStart, viewEnd]);
		}
	}
}
