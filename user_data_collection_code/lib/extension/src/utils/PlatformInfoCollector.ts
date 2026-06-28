import * as os from "os";
import * as vscode from "vscode";
import { getSessionId } from "../extension";

/**
 * @descriptionFunction to generate platform information as URL parameters
 * @returns URL parameters containing IDE, IDE version, OS type, OS version, and extension version
 */

export function getPlatfomInfoAsUrlParams(): string {
	const currentExtention =
		vscode.extensions.getExtension("Anonymous.IDE-Assistant") ||
		vscode.extensions.getExtension("Anonymous.IDE-Assistant-Enterprise") ||
		vscode.extensions.getExtension("Anonymous.IDE-Assistant-SaaS") ||
		vscode.extensions.getExtension("Anonymous.IDE-Assistant-Standard");
	const currentVersion = currentExtention?.packageJSON.version;
	const osType = os.type();
	const osRelease = os.release();
	const sessionId = getSessionId();
	return `ide=vsc&ide_v=${vscode.version}&os=${osType}&os_v=${osRelease}&v=${currentVersion}&sessionId=${sessionId}`;
}
