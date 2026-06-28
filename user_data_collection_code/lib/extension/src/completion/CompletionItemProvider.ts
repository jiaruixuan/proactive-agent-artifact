import * as crypto from "crypto";
import { fetch, ProxyAgent } from "undici";
import * as vscode from "vscode";
import * as zlib from "zlib";
import { ApiKeyManager } from "../ai/ApiKeyManager";
import { disable, getSessionId, globalLogger, startLoading, stopLoading } from "../extension";
import { globalConfig } from "../globalConfig";
import { CompletionStatistics } from "../local_analyze/CompletionStatistics";
import { ProjectCompletion, showInfo } from "../local_analyze/local_analyze2";
import { RecentFileAnalyzer } from "../local_analyze/RecentFileAnalyze";
import { checkProjectCompletionAvailable, GrayTestHelper, notifyInstallLSP } from "../local_analyze/utils";
import { OperationCollection } from "../operation_collection/operationCollection";
import { translate } from "../Translate";
import { getDisplayLanguage } from "../utils/GlobalVariables";
import { getPlatfomInfoAsUrlParams } from "../utils/PlatformInfoCollector";
import requestFetch from "../utils/request_fetch";
import { getServerURL } from "../utils/ServerURL";
import { setChangeFilesBuffer } from "../utils/uploadProject";
import { PostHogProvider } from "../utils/usePostHog";
import { HistoryLoggerManager } from "./editMode";

const MAX_CHAR_NUMBER = 2.2e5;
const truncateLen = 2000;
let completionId = "";
let completion_time = 0;
let completion_text = "";

interface Completion {
	generated_text: string;
	character_delta: number;
	line_delta: number;
}
export function setCompletionId(completion_id: string) {
	completionId = completion_id;
}
export function getCompletionId() {
	return completionId;
}
export function setCompletionTime(time: number) {
	completion_time = time;
}
export function getCompletionTime() {
	return completion_time;
}
export function setCompletionText(text: string) {
	completion_text = text;
}
export function getCompletionText() {
	return completion_text;
}
export interface CompletionResponse {
	request_id: string;
	completions: Completion[];
	context: string;
}

class ShownCompletion {
	public position: vscode.Position | null = null;
	public response: CompletionResponse | null = null;
	constructor(position: vscode.Position | null, response: CompletionResponse | null) {
		this.position = position;
		this.response = response;
	}
}

export class CompletionStatus {
	public document_uri: vscode.Uri | null = null;
	public current_completion: ShownCompletion | null = null;
	public is_sending = false;
	public sending_time = 0;
	public version = 0;
	public last_provide_version = 0;
}

export const completionDecorationType = vscode.window.createTextEditorDecorationType({
	after: {
		contentText: translate("  (Currently no completion options available)", getDisplayLanguage()),
		fontStyle: "italic",
		color: new vscode.ThemeColor("editor.foreground"),
	},
});

// regex to match "fim_prefix", "fim_suffix", "fim_middle", "endoftext"...
const special_token_regex = /<((fim_((prefix)|(suffix)|(middle)))|(\|[a-z]*\|))>/g;

const context_window = 8192;
const check_context_window = 100;
let send_whole_file = true;

let prev_file_name = "";
let prev_file_content = "";
let prev_file_md5 = "";

function clean_diff() {
	prev_file_name = "";
	prev_file_content = "";
	prev_file_md5 = "";
}

function md5(str: string) {
	return crypto.createHash("md5").update(str).digest("hex");
}

function get_diff(new_file_name: string, new_file_content: string) {
	const content_md5 = md5(new_file_content);
	if (new_file_name != prev_file_name) {
		prev_file_name = new_file_name;
		prev_file_content = new_file_content;
		prev_file_md5 = content_md5;
		return {
			plen: 0,
			slen: 0,
			bplen: 0,
			bslen: 0,
			pmd5: "",
			nmd5: content_md5,
			diff: new_file_content,
			filename: new_file_name,
		};
	}
	// common prefix length
	let plen = 0;
	while (plen < new_file_content.length && plen < prev_file_content.length && new_file_content[plen] == prev_file_content[plen]) {
		plen++;
	}
	// common suffix length
	let slen = 0;
	while (
		slen + plen < new_file_content.length &&
		slen + plen < prev_file_content.length &&
		new_file_content[new_file_content.length - slen - 1] == prev_file_content[prev_file_content.length - slen - 1]
	) {
		slen++;
	}
	const encoder = new TextEncoder();
	const binary_plen = encoder.encode(new_file_content.substring(0, plen)).length;
	const binary_slen = encoder.encode(new_file_content.substring(new_file_content.length - slen)).length;
	const result = {
		plen: plen,
		slen: slen,
		bplen: binary_plen,
		bslen: binary_slen,
		pmd5: prev_file_md5,
		nmd5: content_md5,
		diff: new_file_content.substring(plen, new_file_content.length - slen),
		filename: new_file_name,
	};
	prev_file_content = new_file_content;
	prev_file_md5 = content_md5;
	return result;
}

function getContext(
	document: vscode.TextDocument,
	position_left: vscode.Position,
	position_right: vscode.Position,
	context_lenth_half: number
) {
	const doc_lenth = document.offsetAt(document.lineAt(document.lineCount - 1).range.end);
	const left_offset = document.offsetAt(position_left);
	const right_offset = document.offsetAt(position_right);
	const start_offset = Math.max(0, left_offset - context_lenth_half);
	const end_offset = Math.min(doc_lenth, right_offset + context_lenth_half);

	const start = document.positionAt(start_offset);
	const end = document.positionAt(end_offset);

	const prefix = document.getText(new vscode.Range(start, position_left));
	const suffix = document.getText(new vscode.Range(position_right, end));
	return prefix + "<fim_middle>" + suffix;
}

let last_is_pc = false;
export async function sendCompletions(
	user_id: string,
	logger: vscode.OutputChannel,
	document: vscode.TextDocument,
	position: vscode.Position,
	edit_mode: boolean = false,
	trigger_type: string = "0" //0: trigger by hand, 1: auto trigger after normal completion, 2: auto trigger edit completion accept
) {
	try {
		OperationCollection.getInstance().triggerUploadOnCompletionRequest();
	} catch (error) {}

	let prefix = "";
	let suffix = "";
	let context = "";
	PostHogProvider.getInstance()?.capture("completion_triggered", {
		session_id: getSessionId(),
		language: document.languageId,
		trigger_type: trigger_type === "0" ? "manual" : "automatic",
		request_id: "",
	});
	// check if the charNumber > maxCharNumber
	const charNumber = document.offsetAt(document.lineAt(document.lineCount - 1).range.end);
	if (charNumber > MAX_CHAR_NUMBER) {
		send_whole_file = false;
	} else send_whole_file = true;
	// build prompt
	const pc_setting = vscode.workspace.getConfiguration("ideassistant.useProjectCompletion").get("open") as string;
	const is_enterprise = globalConfig.APP_VERSION !== "default";
	const has_lsp = await ProjectCompletion.getInstance().getFileLSP(document);
	if ((pc_setting !== "Off" && is_enterprise) || (pc_setting === "On" && !is_enterprise)) {
		if (has_lsp === 0) {
			notifyInstallLSP(document.languageId);
		}
	}
	let start_cut_chars = 0;
	let end_cut_chars = 0;
	if (send_whole_file) {
		const start = document.positionAt(0);
		const end = document.lineAt(document.lineCount - 1).range.end;
		prefix = document.getText(new vscode.Range(start, position));
		suffix = document.getText(new vscode.Range(position, end));
		context = getContext(document, position, position, check_context_window);
	} else {
		const doc_lenth = document.offsetAt(document.lineAt(document.lineCount - 1).range.end);
		const prompt_length_half = MAX_CHAR_NUMBER / 2;
		const now_offset = document.offsetAt(position);

		const now_offset_left = Math.floor(now_offset / truncateLen) * truncateLen;
		const now_offset_right = doc_lenth - Math.floor((doc_lenth - now_offset) / truncateLen) * truncateLen;
		const start_offset = Math.max(
			0,
			Math.min(
				now_offset_left - prompt_length_half,
				doc_lenth - prompt_length_half * 2
			)
		);
		const end_offset = Math.min(
			doc_lenth,
			Math.max(now_offset_right + prompt_length_half, prompt_length_half * 2)
		);
		let start = document.positionAt(start_offset);
		const now = document.positionAt(now_offset);
		let end = document.positionAt(end_offset);
		if (start.character != 0) {
			start = new vscode.Position(start.line + 1, 0);
		}
		if (end.character != document.lineAt(end.line).range.end.character) {
			end = document.lineAt(end.line - 1).range.end;
		}
		prefix = document.getText(new vscode.Range(start, now));
		suffix = document.getText(new vscode.Range(now, end));
		start_cut_chars = document.offsetAt(start);
		end_cut_chars = doc_lenth - document.offsetAt(end);
	}
	// replace all special_token_regex to ""
	prefix = prefix.replace(special_token_regex, "");
	suffix = suffix.replace(special_token_regex, "");

	let pc_available = false;
	let pc_prompt = "";
	const use_var_type_prompt = await checkProjectCompletionAvailable(user_id, has_lsp);

	if (use_var_type_prompt) {
		console.log("pc_yes");
		pc_available = true;
		pc_prompt = await ProjectCompletion.getInstance().getPrompt(document, position.line);
	}
	if (pc_prompt !== "") {
		last_is_pc = true;
	} else {
		last_is_pc = false;
	}

	// let prompt =
	//  "<fim_prefix>" + prefix + "<fim_suffix>" + suffix + "<fim_middle>";
	const diff_meta_datas: any = get_diff(document.fileName, prefix + suffix);
	const encoder = new TextEncoder();
	diff_meta_datas["cpos"] = prefix.length;
	diff_meta_datas["bcpos"] = encoder.encode(prefix).length;
	diff_meta_datas["pc_available"] = pc_available;
	let rf_prompt = "";
	const debugStr = vscode.workspace.getConfiguration("ideassistant").get("DocumentFile") as string;
	if (
		(await GrayTestHelper.getInstance().getResult(user_id, "recent_files_mode")) ||
		debugStr == "AnonymousDebugDocument"
	) {
		rf_prompt = RecentFileAnalyzer.getInstance().getRFPrompt(document, true, 5, 300);
		diff_meta_datas["pc_available"] = true;
		showInfo(`rf_prompt: ${rf_prompt}`);
	}
	diff_meta_datas["pc_prompt"] = rf_prompt + pc_prompt;
	if (edit_mode) {
		diff_meta_datas["edit_mode"] = "true";
		let history = HistoryLoggerManager.getInstance().getHistory(document, position);
		history = history.slice(start_cut_chars, history.length - end_cut_chars);
		history = history.replace(special_token_regex, "");
		diff_meta_datas["edit_mode_history"] = history;
		diff_meta_datas["edit_mode_trigger_type"] = trigger_type;
	}

	const client_completion_id = `${user_id}_${Date.now()}_${Math.random().toString(36).substring(2, 15)}`;

	// send request
	const params = {
		inputs: "",
		meta_datas: diff_meta_datas,
		completion_id: client_completion_id,
	};
	const start_time = performance.now();
	try {
		const data = JSON.stringify(params);
		const compressed = zlib.gzipSync(data);
		const proxy = vscode.workspace.getConfiguration("http").get("proxy") as string;
		const proxyClient = proxy ? new ProxyAgent(proxy) : undefined;
		const serverURL = getServerURL();
		const fetch_url = `${serverURL}/codeapi/completion/generate_one_stage_auth/${user_id}?${getPlatfomInfoAsUrlParams()}`;
		const customHeaders = {
			"Content-Type": "application/json",
			"Content-Encoding": "gzip",
		};
		const response = await requestFetch.post(fetch_url, undefined, compressed, true, undefined, proxyClient, customHeaders);
		if (!response.ok) {
			const errorInfo = await response.text();
			logger.appendLine(`HTTP Error: ${response.status} ${response.statusText}. Error Info: ${errorInfo}`);
			throw new Error(`HTTP Error: ${response.status} ${response.statusText}. Error Info: ${errorInfo}`);
		}
		const res: any = await response.json();
		logger.appendLine("received " + Math.floor(performance.now() - start_time).toString() + " ms");
		if (res.error == "diff data error") {
			clean_diff(); // clean diff data if diff data error
			console.log("diff data error");
		}
		setCompletionId(client_completion_id);
		setCompletionText(res.generated_text);
		setCompletionTime(start_time);
		if (edit_mode) {
			return res;
		}
		// logger.appendLine(JSON.stringify(res));
		// logger.appendLine(res.generated_text);
		let text = res.generated_text.replace("<|endoftext|>", "");
		const request_id = res.server_request_id;
		const character_delta = res.delta_char;
		const line_delta = res.delta_line;
		const ex_msg = res.ex_msg;
		// character_delta = line_delta = 0;
		// ex_msg = "";
		if (ex_msg) text += ex_msg;

		// logger.appendLine("text: " + text + " character_delta: " + character_delta + " ex_msg: " + ex_msg);

		const out_: Completion = {
			generated_text: text,
			character_delta: character_delta,
			line_delta: line_delta,
		};
		const out: CompletionResponse = {
			request_id: request_id,
			completions: [out_],
			context: context,
		};

		const delayTimeConfig = vscode.workspace.getConfiguration("ideassistant.delayCompletion");
		const delayTime = delayTimeConfig.get("delaytime") as number;
		if (delayTime) {
			await new Promise(resolve => setTimeout(resolve, delayTime));
		}
		return out;
	} catch (error) {
		logger.appendLine("There has been a problem with your fetch operation:" + error);
		console.error("There has been a problem with your fetch operation:", error);
		return null;
	}
}

export async function acceptCompletion(params: any, logger: vscode.OutputChannel, ctx: vscode.ExtensionContext | null) {
	try {
		const params_ = {
			request_id: params.request_id,
			completion_id: getCompletionId(),
		};
		logger.appendLine("accept");
		const proxy = vscode.workspace.getConfiguration("http").get("proxy") as string;
		const proxyClient = proxy ? new ProxyAgent(proxy) : undefined;
		const serverURL = getServerURL();
		const response = await fetch(
			serverURL + "/codeapi/completion/accept/" + params.user_id + "?completion_id=" + getCompletionId(),
			{
				method: "POST",
				headers: {
					"Content-Type": "application/json",
				},
				body: JSON.stringify(params_),
				dispatcher: proxyClient,
			}
		);

		if (!response.ok) {
			const errorInfo = await response.text();
			throw new Error(`HTTP Error: ${response.status} ${response.statusText}. Error Info: ${errorInfo}`);
		}
	} catch (error) {
		console.error("There has been a problem with your fetch operation:", error);
		throw error;
	}
}

async function trigger_edit_completion(user_id: string) {
	const config = vscode.workspace.getConfiguration("ideassistant.useAutoEditCompletion");
	if (config.get("open") === "On") {
		vscode.commands.executeCommand("ideassistant.editCompletion", { trigger_type: "1" });
	} else if (config.get("open") === "Auto") {
		const result = await GrayTestHelper.getInstance().getResult(user_id, "edit_mode_auto");
		if (result === 1) {
			vscode.commands.executeCommand("ideassistant.editCompletion", { trigger_type: "1" });
		}
	}
}

export function captureCompletionAccepted(text: string) {
	if (text === "") return;
	const captureData = {
		completion_id: getCompletionId(),
		accepted_length: text.length,
		time_to_accept: Math.floor(performance.now() - getCompletionTime()),
	};
	PostHogProvider.getInstance()?.capture("completion_accepted", captureData);
}
export function captureCompletionRejected(text: string) {
	if (text === "") return;
	const captureData = {
		completion_id: getCompletionId(),
		reject_length: text.length,
		time_to_reject: Math.floor(performance.now() - getCompletionTime()),
	};
	PostHogProvider.getInstance()?.capture("completion_rejected", captureData);
}

export class IDEAssistantInlineCompletionItemProvider implements vscode.InlineCompletionItemProvider {
	// private lastCompletionItem: vscode.InlineCompletionList | null = null;
	// private req_id: String = "";
	// public current_version: integer = 0;
	// public lastPosition: vscode.Position | null = null;
	public logger: vscode.OutputChannel = globalLogger;
	// public params: any = null;
	// public direct_response: CompletionResponse | null = null;
	// public task_version: integer = -1; // 0: waiting for complete suggestion; 1: complete suggestion outputed; -1: no suggestion now
	// public task_time: number = 0;
	// public last_activate_time: number = 0;
	// public task_uri: vscode.Uri | null = null;
	// public current_completion: ShownCompletion | null = null;
	public extension_context: vscode.ExtensionContext;
	public access_token = "";
	public refresh_token = "";
	public user_id = "";
	public completion_status_dict: { [key: string]: CompletionStatus } = {};
	public sending_count = 0;
	public isTriggeredByShortcut = false;
	public lastChangeWasDeletion = false;
	public completion_statisctics: CompletionStatistics;
	private note_login_dismissed = false;
	private last_note_login_time = 0;
	private note_login_interval = 1000 * 60 * 5; // 5 minutes

	constructor(extension_context: vscode.ExtensionContext, user_id: string) {
		this.extension_context = extension_context;
		this.user_id = user_id;
		this.completion_statisctics = new CompletionStatistics(this.completion_status_dict, this.user_id, this.logger);
		vscode.workspace.onDidChangeTextDocument(this.handleTextDocumentChange);
	}

	private handleTextDocumentChange = (event: vscode.TextDocumentChangeEvent) => {
		setChangeFilesBuffer(event);
		if (vscode.workspace.getConfiguration("ideassistant.inlineCompletion").get("disableCompletionWhenDelete")) {
			// check if press 'Backspace' or 'Delete'
			const changes = event.contentChanges;
			this.lastChangeWasDeletion = changes.some(change => change.text === "" && change.rangeLength > 0);
		}
	};

	checkIsSpecialChars(beforeText: string, afterText: string, specialChars: string[]): boolean {
		const lastBeforeTextChar = beforeText.charAt(beforeText.length - 1);
		const firstAfterTextChar = afterText.charAt(0);

		const specialCharPairs: { [key: string]: string } = {
			"(": ")",
			"{": "}",
			"[": "]",
			"<": ">",
		};

		if (lastBeforeTextChar === firstAfterTextChar && specialChars.includes(lastBeforeTextChar)) {
			return true;
		} else if (specialCharPairs[lastBeforeTextChar] === firstAfterTextChar) {
			return true;
		}

		return false;
	}

	checkBetweenText(document: vscode.TextDocument, position: vscode.Position): boolean {
		const line = position.line;
		const character = position.character;

		// before cursorPosition
		const beforeText = character > 0 ? document.getText(new vscode.Range(new vscode.Position(line, 0), position)) : "";
		const hasValidTextBefore = beforeText.trim().length > 0;

		// after cursorPosition
		const lineEndCharacter = document.lineAt(line).range.end.character;
		const afterText =
			character < lineEndCharacter
				? document.getText(new vscode.Range(position, new vscode.Position(line, lineEndCharacter)))
				: "";
		const hasValidTextAfter = afterText.trim().length > 0;

		const specialChars = ["'", '"', "(", "{", "<", "["];

		if (hasValidTextBefore && hasValidTextAfter && this.checkIsSpecialChars(beforeText, afterText, specialChars)) {
			return false;
		}

		return hasValidTextBefore && hasValidTextAfter;
	}

	async clear_access_token() {
		this.access_token = "";
	}

	async clear_refresh_token() {
		this.refresh_token = "";
	}

	async clear_user_id() {
		this.user_id = "";
	}

	notifyLogin() {
		if (this.note_login_dismissed) {
			return;
		}
		if (Date.now() - this.last_note_login_time < this.note_login_interval) {
			return;
		}
		this.last_note_login_time = Date.now();
		const language = getDisplayLanguage();
		vscode.window
			.showInformationMessage(
				translate("[IDE Assistant] Please login first.", language),
				translate("Login", language),
				translate("Dismiss", language)
			)
			.then(result => {
				if (result === translate("Login", language)) {
					vscode.commands.executeCommand("workbench.view.extension.ideassistant");
				} else if (result === translate("Dismiss", language)) {
					this.note_login_dismissed = true;
				}
			});
		this.logger.appendLine("no key");
	}

	async retrieve_and_set_access_token() {
		const apiKeyManager = new ApiKeyManager({
			secretStorage: this.extension_context!.secrets,
		});
		try {
			await apiKeyManager.hasIDEAssistantAccessToken();
		} catch (e) {
			// console.log("get token error, clean token");
			// await apiKeyManager.clearIDEAssistantAccessToken();
			// console.log("get token error, clean token done");
		}
		if (!(await apiKeyManager.hasIDEAssistantAccessToken())) {
			this.notifyLogin();
			return;
		}
		this.access_token = (await apiKeyManager.getIDEAssistantAccessToken())!.toString();
	}

	async retrieve_and_set_refresh_token() {
		const apiKeyManager = new ApiKeyManager({
			secretStorage: this.extension_context!.secrets,
		});
		try {
			await apiKeyManager.hasIDEAssistantRefreshToken();
		} catch (e) {
			// console.log("get token error, clean token");
			// await apiKeyManager.clearIDEAssistantRefreshToken();
			// console.log("get token error, clean token done");
		}
		if (!(await apiKeyManager.hasIDEAssistantRefreshToken())) {
			this.notifyLogin();
			return;
		}
		this.refresh_token = (await apiKeyManager.getIDEAssistantRefreshToken())!.toString();
	}

	async retrieve_and_set_user_id() {
		const apiKeyManager = new ApiKeyManager({
			secretStorage: this.extension_context!.secrets,
		});
		try {
			await apiKeyManager.hasIDEAssistantUserId();
		} catch (e) {
			// console.log("get userid error, clean userid");
			// await apiKeyManager.clearIDEAssistantUserId();
			// console.log("get userid error, clean userid done");
		}
		if (!(await apiKeyManager.hasIDEAssistantUserId()) && !(await apiKeyManager.getIDEAssistantAIApiKey())) {
			this.notifyLogin();
			return;
		}
		this.user_id = (await apiKeyManager.getIDEAssistantUserId())!.toString();
	}

	check_completion_valid(
		last_position: vscode.Position,
		current_position: vscode.Position,
		text: string,
		document: vscode.TextDocument
	) {
		const last_offset = document.offsetAt(last_position);
		const current_offset = document.offsetAt(current_position);
		if (current_position.isBefore(last_position)) return -1;
		if (!document.validatePosition(last_position) || !document.validatePosition(current_position)) return -1;
		const mid_text = document.getText(new vscode.Range(document.positionAt(last_offset), document.positionAt(current_offset)));
		if (text.startsWith(mid_text)) {
			captureCompletionAccepted(mid_text);
			// -2 all complete : some complete
			return text === mid_text ? -2 : mid_text.length;
		} else {
			captureCompletionRejected(text);
			return -1; // not match
		}
	}

	async provideInlineCompletionItems(
		document: vscode.TextDocument,
		position: vscode.Position,
		context: vscode.InlineCompletionContext,
		token: vscode.CancellationToken
	) {
		// when not trigger by shortcut
		if (!this.isTriggeredByShortcut) {
			// check if the suffix in blacklist
			const disableSpecific = vscode.workspace.getConfiguration("ideassistant.disableSpecificInlineCompletion");
			const disable_list = (disableSpecific.get("suffixes") as string)
				.toLowerCase()
				.split(/;|；/);
			const filename = document.fileName.toLowerCase();
			let disable_completion = false;
			for (const suffix of disable_list) {
				if (filename.endsWith("." + suffix)) {
					disable_completion = true;
					break;
				}
			}
			if (disable_completion) {
				return;
			}
			if (!vscode.workspace.getConfiguration("ideassistant.inlineCompletion").get("enable")) {
				this.logger.appendLine("Inline code completions disabled.");
				return;
			}
			if (this.lastChangeWasDeletion) {
				// check if backspace or delete
				this.lastChangeWasDeletion = false;
				return;
			}
			if (vscode.workspace.getConfiguration("ideassistant.inlineCompletion").get("disableCompletionWithinTheLine")) {
				if (this.checkBetweenText(document, position)) return; // check if cursor is between text
			}
		}
		if (this.user_id == "") {
			if (globalConfig.TOGGLE_LOGIN_MESSAGE_ON_KEYBOARD_INPUT) {
				this.logger.appendLine("Please login first.");
				this.notifyLogin();
			}
			return;
		}
		this.completion_statisctics.update_user_id(this.user_id);
		const uri = document.uri.toString();
		if (!(uri in this.completion_status_dict)) {
			this.completion_status_dict[uri] = new CompletionStatus();
		}
		const status: CompletionStatus = this.completion_status_dict[uri]!;
		status.last_provide_version = document.version;
		status.version += 1;
		const current_version = status.version;

		// check if current completion still avaliable
		if (status.current_completion) {
			const start_time = performance.now();
			setCompletionTime(start_time);
			const last_text = status.current_completion?.response?.completions[0]?.generated_text || "";
			const accept_length = this.check_completion_valid(status.current_completion.position!, position, last_text, document);
			if (accept_length >= 0) {
				const current_context = getContext(document, status.current_completion.position!, position, check_context_window);
				if (current_context == status.current_completion.response?.context) {
					const new_text = last_text.slice(accept_length);
					const completion_item = status.current_completion.response.completions[0]!;

					if (
						new_text ==
						document.getText(
							new vscode.Range(position, position.translate(completion_item.line_delta, completion_item.character_delta))
						)
					) {
						// ATTENTION: this should be checked after each typing
						await trigger_edit_completion(this.user_id);
						return;
					}
					const items = [
						{
							insertText: new_text,
							range: new vscode.Range(position, position.translate(completion_item.line_delta, completion_item.character_delta)),
							command: {
								title: "afterInsert",
								command: "llm.afterInsert",
								arguments: [status.current_completion!.response],
							},
						},
					];
					PostHogProvider.getInstance()?.capture("completion_triggered", {
						session_id: getSessionId(),
						language: document.languageId,
						trigger_type: this.isTriggeredByShortcut ? "manual" : "automatic",
						request_id: "",
					});
					// this.logger.appendLine("accept1 completion " + new_text + " " + completion_item.line_delta + " " + completion_item.character_delta + "position: " + position.line + " " + position.character);
					return new vscode.InlineCompletionList(items);
				} else {
					status.current_completion = null;
				}
			} else {
				status.current_completion = null;
			}
		}

		let have_result = false;
		// send request
		if (status.is_sending && performance.now() - status.sending_time > 30000) status.is_sending = false; // in case the sending thread dead
		if (status.is_sending) {
			// delay 500 ms
			await new Promise(resolve => setTimeout(resolve, 500));
			if (current_version != status.version) return;
			this.logger.appendLine("send request delay");
			this.sending_count += 1;
			startLoading();
			const start_time = performance.now();
			const response: CompletionResponse | null = await sendCompletions(
				this.user_id,
				this.logger,
				document,
				position,
				false,
				this.isTriggeredByShortcut ? "0" : "1"
			);
			this.completion_statisctics.update_completion_time(uri, performance.now() - start_time, last_is_pc);
			this.sending_count -= 1;
			if (this.sending_count == 0) {
				stopLoading();
			}
			if (!response) {
				this.logger.appendLine("response error");
				return;
			}
			if (this.isTriggeredByShortcut) {
				this.isTriggeredByShortcut = false;
				// check if the suffix in blacklist
				const disableSpecific = vscode.workspace.getConfiguration("ideassistant.disableSpecificInlineCompletion");
				const disable_list = (disableSpecific.get("suffixes") as string)
					.toLowerCase()
					.split(/;|；/);
				const filename = document.fileName.toLowerCase();
				let disable_completion = false;
				for (const suffix of disable_list) {
					if (filename.endsWith("." + suffix)) {
						disable_completion = true;
						break;
					}
				}
				if (disable_completion) {
					disable();
				}
				if (!vscode.workspace.getConfiguration("ideassistant.inlineCompletion").get("enable")) {
					this.logger.appendLine("Inline code completions disabled.");
					disable();
				}
				if (response.completions[0]?.generated_text?.length === 0) {
					const range = new vscode.Range(position, position);
					vscode.window.activeTextEditor?.setDecorations(completionDecorationType, [range]);
					const timeOutId = setTimeout(() => {
						vscode.window.activeTextEditor?.setDecorations(completionDecorationType, []);
					}, 2000);
					const changeDisposable = vscode.workspace.onDidChangeTextDocument(e => {
						if (e.document === vscode.window.activeTextEditor?.document) {
							clearTimeout(timeOutId);
							vscode.window.activeTextEditor?.setDecorations(completionDecorationType, []);
							changeDisposable.dispose();
						}
					});
				}
			}
			if (current_version != status.version) return;
			if (status.current_completion) return; // new completion already obtained
			status.current_completion = new ShownCompletion(position, response);
			have_result = true;
		} else {
			status.is_sending = true;
			status.sending_time = performance.now();
			this.logger.appendLine("send request turbo");
			this.sending_count += 1;
			startLoading();
			const start_time = performance.now();
			const response: CompletionResponse | null = await sendCompletions(
				this.user_id,
				this.logger,
				document,
				position,
				false,
				this.isTriggeredByShortcut ? "0" : "1"
			);
			this.completion_statisctics.update_completion_time(uri, performance.now() - start_time, last_is_pc);
			this.sending_count -= 1;
			if (this.sending_count == 0) {
				stopLoading();
			}
			status.is_sending = false;
			if (!response) {
				this.logger.appendLine("response error");
				return;
			}
			if (this.isTriggeredByShortcut) {
				this.isTriggeredByShortcut = false;
				// check if the suffix in blacklist
				const disableSpecific = vscode.workspace.getConfiguration("ideassistant.disableSpecificInlineCompletion");
				const disable_list = (disableSpecific.get("suffixes") as string)
					.toLowerCase()
					.split(/;|；/);
				const filename = document.fileName.toLowerCase();
				let disable_completion = false;
				for (const suffix of disable_list) {
					if (filename.endsWith("." + suffix)) {
						disable_completion = true;
						break;
					}
				}
				if (disable_completion) {
					disable();
				}
				if (!vscode.workspace.getConfiguration("ideassistant.inlineCompletion").get("enable")) {
					this.logger.appendLine("Inline code completions disabled.");
					disable();
				}
				if (response.completions[0]?.generated_text?.length === 0) {
					const range = new vscode.Range(position, position);
					vscode.window.activeTextEditor?.setDecorations(completionDecorationType, [range]);
					const timeOutId = setTimeout(() => {
						vscode.window.activeTextEditor?.setDecorations(completionDecorationType, []);
					}, 2000);
					const changeDisposable = vscode.workspace.onDidChangeTextDocument(e => {
						if (e.document === vscode.window.activeTextEditor?.document) {
							clearTimeout(timeOutId);
							vscode.window.activeTextEditor?.setDecorations(completionDecorationType, []);
							changeDisposable.dispose();
						}
					});
				}
			}
			if (status.current_completion) return; // new completion already obtained
			const editor: vscode.TextEditor | undefined = vscode.window.activeTextEditor;
			if (!editor) return; // no activate editor
			const document_new = editor.document;
			const position_new: vscode.Position = editor.selection.active;
			if (uri != document_new.uri.toString()) return; // switch to another document

			const last_text = response.completions[0]?.generated_text || "";
			if (last_text.length == 0 && position.isEqual(position_new)) {
				await trigger_edit_completion(this.user_id);
				return;
			}
			const accept_length = this.check_completion_valid(position, position_new, last_text, document);
			if (accept_length >= 0) {
				status.current_completion = new ShownCompletion(position, response);
				if (accept_length == 0) {
					const completion_item = response.completions[0]!;
					const items = [
						{
							insertText: last_text,
							range: new vscode.Range(position, position.translate(completion_item.line_delta, completion_item.character_delta)),
							command: {
								title: "afterInsert",
								command: "llm.afterInsert",
								arguments: [status.current_completion!.response],
							},
						},
					];
					// this.logger.appendLine("accept2 completion " + last_text + " " + completion_item.line_delta + " " + completion_item.character_delta + "position: " + position.line + " " + position.character);
					vscode.commands.executeCommand("editor.action.inlineSuggest.trigger");
					return new vscode.InlineCompletionList(items);
				}
				have_result = true;
			}
		}
		if (have_result) {
			vscode.commands.executeCommand("editor.action.inlineSuggest.trigger");
		}
	}
}
