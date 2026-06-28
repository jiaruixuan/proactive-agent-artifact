// import * as vscode from "vscode";
export interface DefaultOptons {
	uuid: string | undefined;
	requestUrl: string | undefined;
	domTracker: boolean;
	extra: Record<string, any> | undefined;
	jsError: boolean;
	whiteScreen: boolean;
	user_id: string;
	maxEvent: number;
}

export interface Options extends Partial<DefaultOptons> {
	requestUrl: string;
}
