import * as vscode from "vscode";
import { globalConfig } from "../globalConfig";

export let serverURL = getServerURL();

vscode.workspace.onDidChangeConfiguration(e => {
	if (e.affectsConfiguration("ideassistant.server.serverURL")) {
		serverURL = getServerURL();
	}
});

export function getServerURL(): string {
	let url = vscode.workspace.getConfiguration("ideassistant.server").get("serverURL") as string | undefined;

	const canCustomUrl = globalConfig.VERSION_NAME === "Enterprise" || globalConfig.VERSION_NAME === "Standard";

	if (url && canCustomUrl) {
		// strip whitespaces and trailing slashes
		url = url.trim().replace(/\/+$/, "");
	} else {
		url = globalConfig.APP_API_URL;
	}

	return url;
}
