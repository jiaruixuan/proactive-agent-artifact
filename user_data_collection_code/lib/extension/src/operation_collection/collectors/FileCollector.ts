import * as vscode from "vscode";
import { FileCreateOperation, FileDeleteOperation, FileRenameOperation, WorkspaceFolderChangeOperation } from "../operations";
import { BaseCollector } from "./base";

/**
 * FileCollector
 *
 */
export class FileCollector extends BaseCollector {
	readonly name = "file";

	protected setupListeners(): void {
		this.disposables.push(
			vscode.workspace.onDidCreateFiles(event => {
				this.handleFileCreate(event);
			})
		);

		this.disposables.push(
			vscode.workspace.onDidDeleteFiles(event => {
				this.handleFileDelete(event);
			})
		);

		this.disposables.push(
			vscode.workspace.onDidRenameFiles(event => {
				this.handleFileRename(event);
			})
		);

		this.disposables.push(
			vscode.workspace.onDidChangeWorkspaceFolders(event => {
				this.handleWorkspaceFolderChange(event);
			})
		);
	}

	private handleFileCreate(event: vscode.FileCreateEvent) {
		event.files.forEach(file => {
			// Only track file scheme URIs, skip untitled and other schemes
			if (file.scheme === "file") {
				const operation = new FileCreateOperation(file.toString());
				this.emit(operation);
			}
		});
	}

	private handleFileDelete(event: vscode.FileDeleteEvent) {
		event.files.forEach(file => {
			// Only track file scheme URIs, skip untitled and other schemes
			if (file.scheme === "file") {
				const operation = new FileDeleteOperation(file.toString());
				this.emit(operation);
			}
		});
	}

	private handleFileRename(event: vscode.FileRenameEvent) {
		event.files.forEach(file => {
			// Only track file scheme URIs, skip untitled and other schemes
			if (file.oldUri.scheme === "file" && file.newUri.scheme === "file") {
				const operation = new FileRenameOperation(file.oldUri.toString(), file.newUri.toString());
				this.emit(operation);
			}
		});
	}

	private handleWorkspaceFolderChange(event: vscode.WorkspaceFoldersChangeEvent) {
		const addedFolders = event.added.map(folder => folder.uri.toString());
		const removedFolders = event.removed.map(folder => folder.uri.toString());

		if (addedFolders.length > 0 || removedFolders.length > 0) {
			const operation = new WorkspaceFolderChangeOperation(addedFolders, removedFolders);
			this.emit(operation);
		}
	}
}
