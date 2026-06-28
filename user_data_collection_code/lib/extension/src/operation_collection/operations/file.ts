import * as path from "path";
import { Operation } from "./base";

export class FileCreateOperation extends Operation {
	readonly role = "user";
	operationType: string = "file_create";
	readableDetails: string;
	fileUri: string;
	fileName: string;

	constructor(fileUri: string) {
		super();
		this.fileUri = fileUri;
		this.fileName = path.basename(fileUri);
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		return `<note>
The user created a new file.
</note>
<file_path>${this.fileUri}</file_path>
<file_name>${this.fileName}</file_name>`;
	}
}

export class FileDeleteOperation extends Operation {
	readonly role = "user";
	operationType: string = "file_delete";
	readableDetails: string;
	fileUri: string;
	fileName: string;

	constructor(fileUri: string) {
		super();
		this.fileUri = fileUri;
		this.fileName = path.basename(fileUri);
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		return `<note>
The user deleted a file.
</note>
<file_path>${this.fileUri}</file_path>
<file_name>${this.fileName}</file_name>`;
	}
}

export class FileRenameOperation extends Operation {
	readonly role = "user";
	operationType: string = "file_rename";
	readableDetails: string;
	oldUri: string;
	newUri: string;
	oldFileName: string;
	newFileName: string;

	constructor(oldUri: string, newUri: string) {
		super();
		this.oldUri = oldUri;
		this.newUri = newUri;
		this.oldFileName = path.basename(oldUri);
		this.newFileName = path.basename(newUri);
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		return `<note>
The user renamed a file.
</note>
<old_file_path>${this.oldUri}</old_file_path>
<new_file_path>${this.newUri}</new_file_path>
<old_file_name>${this.oldFileName}</old_file_name>
<new_file_name>${this.newFileName}</new_file_name>`;
	}
}

export class WorkspaceFolderChangeOperation extends Operation {
	readonly role = "user";
	operationType: string = "workspace_folder_change";
	readableDetails: string;
	addedFolders: string[];
	removedFolders: string[];

	constructor(addedFolders: string[], removedFolders: string[]) {
		super();
		this.addedFolders = addedFolders;
		this.removedFolders = removedFolders;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		let result = `<note>
The user changed workspace folders.
</note>`;
		if (this.addedFolders.length > 0) {
			result += `\n<added_folders>\n`;
			this.addedFolders.forEach(folder => {
				result += `<folder>${folder}</folder>\n`;
			});
			result += `</added_folders>`;
		}
		if (this.removedFolders.length > 0) {
			result += `\n<removed_folders>\n`;
			this.removedFolders.forEach(folder => {
				result += `<folder>${folder}</folder>\n`;
			});
			result += `</removed_folders>`;
		}
		return result;
	}
}
