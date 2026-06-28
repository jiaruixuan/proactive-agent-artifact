import archiver from "archiver";
import crypto from "crypto";
import * as fs from "fs-extra";
import ignore from "ignore";
import os from "os";
import * as path from "path";
import type { PostHog } from "posthog-node";
import { fetch, ProxyAgent } from "undici";
import * as vscode from "vscode";
import type { ResponseData } from "../agentServerWeb/utils/getIndexProgress";
import type { AIClient } from "../ai/AIClient";
import { ApiKeyManager } from "../ai/ApiKeyManager";
import { globalConfig } from "../globalConfig";
import { isPathIgnored } from "../local_analyze/utils";
import { OperationCollection } from "../operation_collection/operationCollection";
import { getPlatfomInfoAsUrlParams } from "../utils/PlatformInfoCollector";
import { getServerURL } from "../utils/ServerURL";
import { PostHogProvider } from "../utils/usePostHog";
import type { WebviewContainer } from "../webview/WebviewContainer";
import { getContext } from "./GlobalVariables";
import { isBinary } from "./IsBinaryPath";
import KnowledgeBaseStatus from "./KnowledgeBaseStatus";
import { getPostHogInfo } from "./usePostHog";

export class CanSendProject {
	private static instance: CanSendProject | null = null;
	private value: boolean = true;
	private constructor() {}
	public static getInstance(): CanSendProject {
		if (CanSendProject.instance === null) {
			CanSendProject.instance = new CanSendProject();
		}
		return CanSendProject.instance;
	}
	public setValue(newValue: boolean): void {
		this.value = newValue;
	}
	public getValue(): boolean {
		return this.value;
	}
}
let sendSuccess = "unused";
const needRetry = true;
export class UploadProject {
	private fileAndDirectoryNames: string[] = [];
	private fileContent: string[] = [];
	private fileHash: string[] = [];
	private projectPathName: string = "";
	private projectName: string = "";
	private numOverThanLimit: number = 0;
	private sizeOverThanLimit: number = 0;
	private userId: string = "";
	private limit = 50 * 1024 * 1024;
	private file_size_limit = 300 * 1024;
	private file_num_limit = 500;
	private client: PostHog | null = null;
	private overSizeContent = "";
	private overSizeFileName = "";
	private oversizePost = true;
	private uploadError = "";
	private webview: WebviewContainer | undefined;

	constructor({ id, client, webview }: { id: string; client: any; webview: WebviewContainer | undefined }) {
		this.client = client;
		this.userId = id;
		this.webview = webview;
		this.projectPathName = get_project_path_name();
		this.projectName = "project";
		this.initialize();
		const workspaceFolders = vscode.workspace.workspaceFolders;
		if (workspaceFolders && workspaceFolders.length > 0) {
			let folder = workspaceFolders[0]!.uri.fsPath;
			folder = folder.substring(folder.substring(0, folder.length - 1).lastIndexOf("/") + 1);
			this.projectName = folder.substring(folder.substring(0, folder.length - 1).lastIndexOf("\\") + 1);
		}
	}

	async initialize(): Promise<void> {
		const matchedFlagPayload = await this.client!.getFeatureFlagPayload("upload-project", this.userId);
		if (matchedFlagPayload) {
			const payload = matchedFlagPayload as { file_num_limit: number; file_size_limit: number };
			this.file_num_limit = payload?.file_num_limit;
			this.file_size_limit = payload?.file_size_limit;
		}
	}
	async getIndexProgress() {
		const [res, uploadError] = (await getIndexProgress(this.projectPathName, this.userId)) as [ResponseData, string];
		this.uploadError = uploadError;
		return res;
	}

	async getProjectFiles(isLoop: boolean = false) {
		const res = await this.getIndexProgress();
		if (res) {
			let is_Init = false;
			const { index_status = "init" } = res;
			if (index_status === "init" || index_status === "success" || index_status === "error") {
				if (!isLoop) {
					is_Init = true;
				} else {
					if (index_status == "success") {
						is_Init = false;
					} else {
						is_Init = true;
					}
				}
				const state = { total_size: 0, total_num: 0 };
				const workspaceFolders = vscode.workspace.workspaceFolders;
				if (workspaceFolders) {
					await this.sendUserUpdateFile(state, is_Init);
				}
				this.client &&
					this.client.capture({
						distinctId: this.userId,
						event: "init upload project",
						properties: {
							isOverFlow: this.numOverThanLimit > 0,
							sizeOverThanLimit: this.sizeOverThanLimit,
							totalSize: state.total_size,
							totalNum: state.total_num,
							updateStatus: sendSuccess,
							overSizeFileName: this.overSizeFileName,
							overSizeContent: this.overSizeContent,
							uploadError: this.uploadError,
						},
					});
			}
		}
	}
	is_binary_string(str: string) {
		// textchars = bytearray({7,8,9,10,12,13,27} | set(range(0x20, 0x100)) - {0x7f})
		// is_binary_string = lambda bytes: bool(bytes.translate(None, textchars))
		const binary_regex = /[^\u0007\u0008\u0009\u000A\u000C\u000D\u001B\u0020-\u007E\u0080-\u0100]/;
		return binary_regex.test(str);
	}
	async getFilesName(
		folderPath: string,
		directoryName: string = "",
		level: number = 0,
		state: any = {},
		max_length?: number
	) {
		if (globalConfig.APP_VERSION == "default") {
			if (level > 10) return;
			if (state.total_size && state.total_size > this.limit) return;
		}
		const contents = fs.readdirSync(folderPath);
		const excludeSettings: { [key: string]: string } | undefined = vscode.workspace.getConfiguration("files").get("exclude");
		let filesExcludeRules: string[] = [];
		if (typeof excludeSettings === "object" && excludeSettings !== null) {
			filesExcludeRules = Object.keys(excludeSettings).filter(key => excludeSettings[key]);
		}
		for (const content of contents) {
			const contentPath = path.join(folderPath, content);
			const result = await isPathIgnored(contentPath);
			if (result) {
				continue;
			}
			if (content.startsWith(".")) {
				continue;
			}
			const concatenatedContent = this.fileContent.join("");
			const totalLength = concatenatedContent.length;
			if (max_length && totalLength > max_length) {
				return;
			}
			if (fs.lstatSync(contentPath).isDirectory()) {
				const newDirectoryName = directoryName ? `${directoryName}/${content}` : content;
				this.fileAndDirectoryNames.push(`${this.projectPathName}/${this.projectName}/` + newDirectoryName + "/");
				this.fileHash.push("D_" + newDirectoryName + "/");
				this.fileContent.push("");
				await this.getFilesName(contentPath, newDirectoryName, level + 1, state, max_length);
			} else {
				if (state.total_num && state.total_num > this.file_num_limit) {
					this.numOverThanLimit = 1;
					return;
				}

				const fileName = directoryName ? `${directoryName}/${content}` : content;
				if (isBinary(fileName)) {
					continue;
				}
				if (!fs.lstatSync(contentPath).isFile()) {
					continue;
				}
				const fileBuffer_binary = fs.readFileSync(contentPath, "binary");
				if (this.is_binary_string(fileBuffer_binary.toString())) {
					continue;
				}
				const fileBuffer = fs.readFileSync(contentPath, "binary");
				const file_size = fs.lstatSync(contentPath).size;
				if (file_size > this.file_size_limit) {
					this.sizeOverThanLimit++;
					if (this.oversizePost) {
						this.oversizePost = false;
						const fileContent = fileBuffer.toString();
						const lines = fileContent.split("\n");
						const selectedLines: string[] = [];
						if (lines.length > 100) {
							for (let i = 0; i < 100; i++) {
								const randomIndex = Math.floor(Math.random() * lines.length);
								if (lines[randomIndex]) {
									selectedLines.push(lines[randomIndex] as string);
								}
							}
							this.overSizeContent = selectedLines.join("\n");
						}
						const maxSize = 10 * 1024;
						if (this.overSizeContent.length > maxSize) {
							this.overSizeContent = this.overSizeContent.substring(0, maxSize);
						}
						this.overSizeFileName = fileName;
					}
					continue;
				}
				this.fileAndDirectoryNames.push(`${this.projectPathName}/${this.projectName}/${fileName}`);
				const hashSum = crypto.createHash("md5");
				hashSum.update(fileBuffer_binary);
				const hash = hashSum.digest("hex");
				this.fileHash.push(`F_${fileName}${hash}`);
				this.fileContent.push(fileBuffer_binary.toString());
				state.total_size += file_size;
				state.total_num++;
				if (globalConfig.APP_VERSION == "default" && state.total_size > this.limit) return;
			}
		}
	}
	async sendUserUpdateFile(state: any = {}, is_init: boolean = false) {
		const workspaceFolders = vscode.workspace.workspaceFolders;
		this.fileAndDirectoryNames = [];
		this.fileContent = [];
		this.fileHash = [];
		let need_send: string[] = [];
		const proxy = vscode.workspace.getConfiguration("http").get("proxy") as string;
		const proxyClient = proxy ? new ProxyAgent(proxy) : undefined;
		if (workspaceFolders) {
			const folder = workspaceFolders[0]!;
			await this.getFilesName(folder.uri.fsPath, "", 0, state);
			if (is_init) {
				sendSuccess = "init";
				try {
					// const serverURL = getServerURL();
					const url = `https://assistant.example.com/codeapi/try_completion/save_file_and_directory_names?ft_token=${this.userId}&${getPlatfomInfoAsUrlParams()}`;
					// let url = `${serverURL}/codeapi/rag/save_file_and_directory_names?ft_token=${this.userId}&${getPlatfomInfoAsUrlParams()}`;
					const response = await fetch(url, {
						method: "POST",
						headers: {
							"Content-Type": "application/json",
						},
						body: JSON.stringify({
							inputs: "",
							ft_token: this.userId,
							meta_datas: {
								file_dir_names: JSON.stringify(this.fileAndDirectoryNames),
								file_hash: JSON.stringify(this.fileHash),
							},
						}),
						dispatcher: proxyClient,
					});
					interface ResponseData {
						paths?: string[];
						message?: string;
					}
					if (response.ok) {
						const res = (await response.json()) as ResponseData;
						if (res && res.message && res.message === "No change") {
							sendSuccess = "No change";
						}
						if (res && res.paths) {
							need_send = res.paths;
						}
					}
				} catch (error: any) {
					this.uploadError = error.message;
					if (error.name === "AbortError") {
						return;
					}
					// console.error("file upload error", error);
					return;
				}
			} else {
				sendSuccess = "update";
				const bufferInstance = changeFilesBuffer.getInstance();
				need_send = bufferInstance.getFileBuffer();
			}

			if (need_send.length == 0) {
				return;
			}

			const tempDirPath = fs.mkdtempSync(path.join(os.tmpdir(), "addfile"));
			try {
				need_send.forEach((filePath, index) => {
					let tmp_path = filePath;
					tmp_path = tmp_path.substring(tmp_path.indexOf("/") + 1);
					tmp_path = tmp_path.substring(tmp_path.indexOf("/") + 1);
					const targetDir = path.join(tempDirPath, String(index + 1).padStart(8, "0"));
					fs.ensureDirSync(targetDir);
					const fileName = path.basename(tmp_path);
					const destPath = path.join(targetDir, fileName);
					for (const folder of workspaceFolders) {
						const full_path = path.join(folder.uri.fsPath, tmp_path);
						fs.copyFileSync(full_path, destPath);
					}
				});

				const parentDir = path.dirname(tempDirPath);
				const zipFilePath = path.join(parentDir, this.projectPathName + ".zip");
				try {
					await createZipFile(tempDirPath, zipFilePath);
				} catch (error: any) {
					this.uploadError = "createZipFile error";
					return;
				}

				interface ResponseData {
					message?: string;
				}
				try {
					const data = fs.readFileSync(zipFilePath).toString("base64");
					// const serverURL = getServerURL();
					const url = `https://assistant.example.com/codeapi/try_completion/add_files_and_directories?ft_token=${this.userId}&${getPlatfomInfoAsUrlParams()}`;
					const addFileRes = await fetch(url, {
						method: "POST",
						headers: {
							"Content-Type": "application/json",
						},
						body: JSON.stringify({
							inputs: data,
							ft_token: this.userId,
							meta_datas: {
								file_name: this.projectPathName + ".zip",
								file_paths: JSON.stringify(need_send),
							},
						}),
						dispatcher: proxyClient,
					});
					if (addFileRes.ok && is_init) {
						const res = (await addFileRes.json()) as ResponseData;
					}
				} catch (error: any) {
					this.uploadError = "file upload error";
				}
			} catch (error: any) {
				this.uploadError = error.message;
				if (error.name === "AbortError") {
					return;
				}
			} finally {
				fs.removeSync(tempDirPath);
			}
		}
	}

	async showProgress() {
		let process_percent = 0;
		let index_status = "indexing";
		const canSendProjectInstance = CanSendProject.getInstance();
		const maxSteps = 100;
		let currentStep = 0;

		while (currentStep < maxSteps) {
			try {
				const res = await this.getIndexProgress();
				res && ({ index_status = "init", process_percent = 0 } = res);
				if (index_status === "success") {
					canSendProjectInstance.setValue(true);
					sendSuccess = "initSuccess";
					this.webview?.updateUploadProcess(100);
					return;
				} else if (index_status === "error") {
					canSendProjectInstance.setValue(true);
					sendSuccess = "initError";
					this.webview?.updateUploadProcess(100);
					return;
				} else {
					canSendProjectInstance.setValue(false);
				}
				if (index_status === "indexing") {
					const newTotalProcess = Math.floor(process_percent * 100);
					this.webview?.updateUploadProcess(newTotalProcess);
					if (newTotalProcess === 100) return;
					await new Promise(resolve => setTimeout(resolve, 5000));
					currentStep = newTotalProcess;
				}
			} catch (e) {
				return;
			}
		}
	}

	async updateProject() {
		const intervalId = setInterval(async () => {
			try {
				let index_status = "init";
				const res = await this.getIndexProgress();
				res && ({ index_status = "init" } = res);
				if (index_status === "indexing" || index_status === "success" || index_status === "error") {
					clearInterval(intervalId);
					index_status === "indexing" && (await this.showProgress());
				}
			} catch (e: any) {
				this.uploadError = e.message;
				clearInterval(intervalId);
			}
		}, 5000);
	}
}

interface ProgressResponseData {
	index_status?: string;
	process_percent?: number;
}

export function runUploadProject(ai: AIClient, context: vscode.ExtensionContext) {
	if (getUploadProjectConfig() === "Off") {
		return;
	}
	let intervalId: NodeJS.Timeout;
	setTimeout(async () => {
		let postHogInfo = await getPostHogInfo(ai);
		if (!postHogInfo) {
			return;
		}

		let { client, isMyFlagEnabledForUser, delayTime, isOPcollection } = postHogInfo;

		const operationCollection = OperationCollection.getInstance();
		if (isOPcollection) {
			console.log("[OperationCollection] Enabled by PostHog op_collection flag");
			operationCollection.setAvailable(true);
		} else {
			console.log("[OperationCollection] Disabled by PostHog op_collection flag");
			console.log(postHogInfo)
			operationCollection.setAvailable(false);
		}
		// operationCollection.setAvailable(true);

		const apiKeyManager = new ApiKeyManager({
			secretStorage: context.secrets,
		});
		const ctx = getContext();
		const hasRagFlow = ctx?.globalState.get("hasRagFlow") === "true";
		if (hasRagFlow) {
			KnowledgeBaseStatus.getInstance();
		}
		const userId = (await apiKeyManager.getIDEAssistantUserId()) || "";
		if (isMyFlagEnabledForUser) {
			vscode.commands.executeCommand("ideassistant.uploadProject", { client, userId, isLoop: false });
			const run = async () => {
				postHogInfo = await getPostHogInfo(ai);
				if (!postHogInfo) {
					clearInterval(intervalId);
					return;
				}
				({ isMyFlagEnabledForUser, delayTime } = postHogInfo);
				if (delayTime <= 1000 || getUploadProjectConfig() === "Off" || !isMyFlagEnabledForUser) {
					clearInterval(intervalId);
					return;
				}
				const canSendProjectInstance = CanSendProject.getInstance();
				const canSendProject = canSendProjectInstance.getValue();
				if (isMyFlagEnabledForUser && canSendProject) {
					vscode.commands.executeCommand("ideassistant.uploadProject", { client, userId, isLoop: true });
				}
				clearInterval(intervalId);
				intervalId = setInterval(run, delayTime);
			};
			intervalId = setInterval(run, delayTime);
		}
	}, 15000);
}

export function getUploadProjectConfig() {
	const cfg = vscode.workspace.getConfiguration("ideassistant.useAutoUploadProject").get("open");
	if (cfg == "Auto") {
		return "Auto";
	} else if (cfg == "On") {
		return "On";
	} else if (cfg == "Off") {
		return "Off";
	}
}

export class changeFilesBuffer {
	private static instance: changeFilesBuffer;
	private fileBuffer: string[] = [];
	private needSendPath: string = "";

	private constructor() {
		this.needSendPath = getNeedSendPath();
	}

	public static getInstance(): changeFilesBuffer {
		if (!changeFilesBuffer.instance) {
			changeFilesBuffer.instance = new changeFilesBuffer();
		}
		return changeFilesBuffer.instance;
	}

	public setFileBuffer(files: string[]): void {
		files.forEach(file => {
			const filePath = this.needSendPath + "/" + file;
			if (!this.fileBuffer.includes(filePath)) {
				this.fileBuffer.push(filePath);
			}
		});
	}

	public getFileBuffer(): string[] {
		const files = this.fileBuffer.slice();
		this.clearFileBuffer();
		return files;
	}

	public clearFileBuffer(): void {
		this.fileBuffer = [];
	}
}

export function setChangeFilesBuffer(event: vscode.TextDocumentChangeEvent) {
	const tag = getUploadProjectConfig();
	if (tag !== "Off") {
		const document = event.document;
		const workspaceFolder = vscode.workspace.getWorkspaceFolder(document.uri);
		if (workspaceFolder) {
			const folderPath = getFolderPath();
			const bufferInstance = changeFilesBuffer.getInstance();
			const newFileName = document.fileName.startsWith(folderPath) ? document.fileName.substring(folderPath.length + 1) : "";
			bufferInstance.setFileBuffer([newFileName]);
		}
	}
}
export async function getIndexProgress(projectPathName?: string, userId?: string) {
	PostHogProvider.getInstance()?.capture("upload_project_record", {
		projectPathName: projectPathName,
	});
	try {
		const proxy = vscode.workspace.getConfiguration("http").get("proxy") as string;
		const proxyClient = proxy ? new ProxyAgent(proxy) : undefined;
		const url = `https://assistant.example.com/codeapi/try_completion/execution_progress`;
		const response = await fetch(url, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
			},
			body: JSON.stringify({
				inputs: "",
				ft_token: userId,
				meta_datas: {
					project_id: projectPathName,
				},
			}),
			dispatcher: proxyClient,
		});
		if (response.ok) {
			const res = (await response.json()) as ResponseData;
			if (res && res.index_status) {
				return [res, null];
			}
		} else {
			return [null, null];
		}
	} catch (error: any) {
		return [null, error.message];
	}
}
export function get_project_path_name() {
	const workspaceFolders = vscode.workspace.workspaceFolders;
	let project_name = "";
	if (workspaceFolders && workspaceFolders.length > 0) {
		const folder = workspaceFolders[0]!;
		const illegalChars = /[/\\:*?"<>|]/g;
		project_name = folder.uri.fsPath.replace(illegalChars, "__");
		if (project_name.length > 200) {
			project_name = project_name.substring(project_name.length - 200, 200);
		}
	} else {
		project_name = "workspace";
	}
	return project_name;
}

function getFolderPath() {
	const workspaceFolders = vscode.workspace.workspaceFolders;
	let folderPath = "";
	if (workspaceFolders && workspaceFolders.length > 0) {
		const folder = workspaceFolders[0]!;
		folderPath = folder.uri.fsPath;
	}
	return folderPath;
}

function getNeedSendPath() {
	const beforePath = get_project_path_name();
	let project_name = "";
	let filePath = "";
	const folderPath = getFolderPath();
	project_name = folderPath.substring(folderPath.lastIndexOf("/") + 1);
	filePath = beforePath + "/" + project_name;
	return filePath;
}

async function createZipFile(directoryPath: string, zipFilePath: string): Promise<void> {
	return new Promise((resolve, reject) => {
		const output = fs.createWriteStream(zipFilePath);
		const archive = archiver("zip", {
			zlib: { level: 9 },
		});

		output.on("finish", resolve);
		archive.on("error", reject);

		archive.pipe(output);

		archive.glob("**", { cwd: directoryPath, dot: true });
		archive.finalize();
	});
}
