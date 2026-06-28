import * as vscode from "vscode";
import type { ChatPanel } from "../chat/ChatPanel";

let context: vscode.ExtensionContext | undefined;
let chatPanel: ChatPanel | undefined;
export const setContext = (ctx: vscode.ExtensionContext) => {
	context = ctx;
};

export const getContext = (): vscode.ExtensionContext | undefined => {
	return context;
};

export const setChatPanel = (panel: ChatPanel) => {
	chatPanel = panel;
};

export const getChatPanel = (): ChatPanel | undefined => {
	return chatPanel;
};

export const getDisplayLanguage = (): string => {
	let language = "en";
	if (vscode.env.language.startsWith("zh")) {
		language = "zh-cn";
	}
	try {
		if (process.env.LANG && process.env.LANG.startsWith("zh")) {
			language = "zh-cn";
		}
	} catch (e) {
		console.log(e);
	}
	const displayLanguagePreference = vscode.workspace
		.getConfiguration("ideassistant.languagePreference")
		.get("displayPreference") as string;
	if (displayLanguagePreference !== "Auto") {
		language = displayLanguagePreference;
	}

	return language;
};

export const getWorkspaceRoot = (): string => {
	const workspaceFolders = vscode.workspace.workspaceFolders;
	if (workspaceFolders && workspaceFolders.length > 0) {
		const rootPath = workspaceFolders[0]?.uri.fsPath;
		return rootPath ? rootPath : "";
	}
	return "";
};
