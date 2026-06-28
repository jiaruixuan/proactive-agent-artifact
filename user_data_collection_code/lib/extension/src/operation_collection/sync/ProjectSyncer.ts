
import archiver from "archiver";
import * as crypto from "crypto";
import * as fs from "fs-extra";
import * as os from "os";
import * as path from "path";
import { fetch, ProxyAgent } from "undici";
import * as vscode from "vscode";
import { isPathIgnored } from "../../local_analyze/utils";
import { getContext } from "../../utils/GlobalVariables";
import { encryptJSON } from "../utils/encryption";
import { safeError, safeWarn } from "../utils/sanitize";

const MAX_FILE_SIZE = 200 * 1024;
const MAX_PROJECT_SIZE = 50 * 1024 * 1024;
const MAX_FILE_COUNT = 100000;
const MAX_FOLDER_DEPTH = 15;

export interface ProjectSyncerOptions {
	serverUrl: string;
	enabled?: boolean;
	respectGitignore?: boolean;
	proxy?: string;
	timeoutMs?: number;
}

interface FileInfo {
	relativePath: string;
	fullPath: string;
	isDirectory: boolean;
	hash: string;
	size: number;
}

export interface SyncResult {
	success: boolean;
	fileCount: number;
	message: string;
	durationMs: number;
}

interface SyncRecord {
	lastSyncTime: number;
}

export class ProjectSyncer {
	private readonly serverUrl: string;
	private readonly timeoutMs: number;
	private readonly proxyAgent?: ProxyAgent;
	private readonly enabled: boolean;
	private readonly respectGitignore: boolean;

	private locked: boolean = false;
	private workspacePath: string = "";
	private projectName: string = "";
	private projectPathName: string = "";
	private projectId: string = "";

	private readonly SYNC_RECORD_KEY = "ideassistant.projectSync.records";

	private static hasCleanedLegacyFiles = false;

	constructor(options: ProjectSyncerOptions) {
		this.serverUrl = options.serverUrl.replace(/\/$/, "");
		this.enabled = options.enabled ?? false;
		this.respectGitignore = options.respectGitignore ?? true;
		this.timeoutMs = options.timeoutMs ?? 60000;

		const proxy = options.proxy || (vscode.workspace.getConfiguration("http").get("proxy") as string);
		if (proxy) {
			this.proxyAgent = new ProxyAgent(proxy);
		}

		this._initWorkspaceInfo();

		if (!ProjectSyncer.hasCleanedLegacyFiles) {
			setImmediate(() => {
				if (!ProjectSyncer.hasCleanedLegacyFiles) {
					ProjectSyncer.hasCleanedLegacyFiles = true;
					this._cleanupLegacyTempFiles();
				}
			});
		}
	}

	async sync(userId: string): Promise<SyncResult> {
		const startTime = Date.now();

		if (!ProjectSyncer.hasCleanedLegacyFiles) {
			ProjectSyncer.hasCleanedLegacyFiles = true;
			this._cleanupLegacyTempFiles();
		}

		if (!this.enabled) {
			return { success: false, fileCount: 0, message: "Project sync is disabled", durationMs: Date.now() - startTime };
		}
		if (!this.workspacePath) {
			return { success: false, fileCount: 0, message: "No workspace folder found", durationMs: Date.now() - startTime };
		}
		if (this.locked) {
			return { success: false, fileCount: 0, message: "Sync already in progress", durationMs: Date.now() - startTime };
		}

		if (!(await this._shouldSync())) {
			return {
				success: false,
				fileCount: 0,
				message: "Already synced recently, skipping",
				durationMs: Date.now() - startTime,
			};
		}

		this.locked = true;
		try {
			const result = await this._doSync(userId, startTime);
			if (result.success) {
				await this._updateSyncRecord();
			}
			return result;
		} finally {
			this.locked = false;
		}
	}

	private _initWorkspaceInfo(): void {
		const folders = vscode.workspace.workspaceFolders;
		if (!folders?.[0]) return;

		this.workspacePath = folders[0].uri.fsPath;
		const parts = this.workspacePath.split(/[/\\]/);
		this.projectName = parts[parts.length - 1] || "project";
		this.projectPathName = this.workspacePath.replace(/[/\\:*?"<>|]/g, "__").slice(-200);
		this.projectId = crypto.createHash("md5").update(this.workspacePath).digest("hex");
	}

	private _cleanupLegacyTempFiles(): void {
		try {
			const tmpDir = os.tmpdir();
			const entries = fs.readdirSync(tmpDir);

			const TEMP_DIR_PATTERN = /^project-sync-[a-zA-Z0-9]{6}$/;

			let cleanedCount = 0;
			for (const entry of entries) {
				if (!TEMP_DIR_PATTERN.test(entry)) {
					continue;
				}

				const fullPath = path.join(tmpDir, entry);

				try {
					const stat = fs.statSync(fullPath);

					if (!stat.isDirectory()) {
						continue;
					}

					const dirContents = fs.readdirSync(fullPath);
					const hasValidStructure =
						dirContents.length === 0 ||
						dirContents.every(name => {
							return /^\d{8}$/.test(name) || name.endsWith(".zip");
						});

					if (!hasValidStructure) {
						continue;
					}

					let isDefinitelyOurs = true;
					for (const name of dirContents) {
						if (/^\d{8}$/.test(name)) {
							const subDirPath = path.join(fullPath, name);
							try {
								const subStat = fs.statSync(subDirPath);
								if (subStat.isDirectory()) {
									const subContents = fs.readdirSync(subDirPath);
									if (subContents.length > 5) {
										isDefinitelyOurs = false;
										break;
									}
								}
							} catch {
								isDefinitelyOurs = false;
								break;
							}
						}
					}

					if (isDefinitelyOurs) {
						fs.removeSync(fullPath);
						cleanedCount++;
					}
				} catch (error) {
					safeWarn(`[ProjectSyncer]`, `Failed to remove legacy temp dir: ${entry}`, error);
				}
			}

			if (cleanedCount > 0) {
				console.log(`[ProjectSyncer] Cleaned up ${cleanedCount} legacy temp folder(s)`);
			}
		} catch (error) {
			safeWarn(`[ProjectSyncer]`, "Failed to cleanup legacy temp files", error);
		}
	}

	private async _shouldSync(): Promise<boolean> {
		const context = getContext();
		if (!context) {
			return true;
		}

		const records = context.globalState.get<Record<string, SyncRecord>>(this.SYNC_RECORD_KEY, {});
		const record = records[this.projectId];

		if (!record) {
			return true;
		}

		const timeSinceLastSync = Date.now() - record.lastSyncTime;
		const SYNC_INTERVAL_MS = 24 * 60 * 60 * 1000;
		return timeSinceLastSync >= SYNC_INTERVAL_MS;
	}

	private async _updateSyncRecord(): Promise<void> {
		const context = getContext();
		if (!context) {
			return;
		}

		const records = context.globalState.get<Record<string, SyncRecord>>(this.SYNC_RECORD_KEY, {});
		records[this.projectId] = {
			lastSyncTime: Date.now(),
		};
		await context.globalState.update(this.SYNC_RECORD_KEY, records);
	}

	private async _doSync(userId: string, startTime: number): Promise<SyncResult> {
		try {
			const files = await this._scanFiles();
			if (files.length === 0) {
				return { success: true, fileCount: 0, message: "No files to sync", durationMs: Date.now() - startTime };
			}

			const needSendPaths = await this._syncStructure(userId, files);
			if (!needSendPaths || needSendPaths.length === 0) {
				return { success: true, fileCount: 0, message: "No changes to sync", durationMs: Date.now() - startTime };
			}

			await this._syncContent(userId, files, needSendPaths);
			return {
				success: true,
				fileCount: needSendPaths.length,
				message: `Synced ${needSendPaths.length} files`,
				durationMs: Date.now() - startTime,
			};
		} catch (error) {
			safeError(`[ProjectSyncer]`, "Sync failed", error);
			return { success: false, fileCount: 0, message: `Sync failed: ${error}`, durationMs: Date.now() - startTime };
		}
	}

	private async _scanFiles(): Promise<FileInfo[]> {
		const files: FileInfo[] = [];
		let totalSize = 0;
		let fileCount = 0;

		const queue: Array<{ path: string; relativePath: string; depth: number }> = [
			{ path: this.workspacePath, relativePath: "", depth: 0 },
		];

		while (queue.length > 0) {
			const { path: currentPath, relativePath, depth } = queue.shift()!;
			if (depth > MAX_FOLDER_DEPTH) continue;

			let contents: string[];
			try {
				contents = await fs.readdir(currentPath);
			} catch {
				continue;
			}

			for (const name of contents) {
				if (name.startsWith(".")) continue;

				const fullPath = path.join(currentPath, name);
				const fileRelativePath = relativePath ? `${relativePath}/${name}` : name;

				let stats: fs.Stats;
				try {
					stats = await fs.lstat(fullPath);
				} catch {
					continue;
				}

				if (stats.isSymbolicLink()) continue;

				if (stats.isDirectory()) {
					files.push({
						relativePath: fileRelativePath + "/",
						fullPath,
						isDirectory: true,
						hash: `D_${fileRelativePath}/`,
						size: 0,
					});
					queue.push({ path: fullPath, relativePath: fileRelativePath, depth: depth + 1 });
				} else if (stats.isFile()) {
					if (stats.size > MAX_FILE_SIZE) continue;

					if (this.respectGitignore && (await isPathIgnored(fullPath))) continue;

					let content: string;
					try {
						content = await fs.readFile(fullPath, "binary");
						if (this._isBinaryString(content)) continue;
					} catch {
						continue;
					}

					const hash = crypto.createHash("md5").update(content).digest("hex");
					files.push({
						relativePath: fileRelativePath,
						fullPath,
						isDirectory: false,
						hash: `F_${fileRelativePath}${hash}`,
						size: stats.size,
					});

					totalSize += stats.size;
					fileCount++;
					if (totalSize > MAX_PROJECT_SIZE || fileCount > MAX_FILE_COUNT) {
						safeWarn(`[ProjectSyncer]`, "Size or count limit reached");
						return files;
					}
				}
			}
		}

		return files;
	}

	private _isBinaryString(str: string): boolean {
		return /[\x00-\x06\x0E-\x1A\x1C-\x1F]/.test(str);
	}

	private async _syncStructure(userId: string, files: FileInfo[]): Promise<string[]> {
		const fileNames = files.map(f => `${this.projectPathName}/${this.projectName}/${f.relativePath}`);
		const fileHashes = files.map(f => f.hash);

		const requestData = {
			ft_token: userId,
			meta_datas: {
				file_dir_names: JSON.stringify(fileNames),
				file_hash: JSON.stringify(fileHashes),
				project_name: this.projectName,
				project_path_name: this.projectPathName,
				project_id: this.projectId,
				user_id: userId,
			},
		};

		const encryptedData = encryptJSON(requestData);

		const response = await fetch(`${this.serverUrl}/sync_repo_structure`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({
				encrypted: true,
				data: encryptedData,
			}),
			dispatcher: this.proxyAgent,
			signal: AbortSignal.timeout(this.timeoutMs),
		});

		if (!response.ok) {
			throw new Error(`sync_repo_structure failed: HTTP ${response.status}`);
		}

		const result = (await response.json()) as { paths?: string[] };
		return result.paths || [];
	}

	private async _syncContent(userId: string, files: FileInfo[], needSendPaths: string[]): Promise<void> {
		const pathToFile = new Map<string, FileInfo>();
		for (const file of files) {
			pathToFile.set(`${this.projectPathName}/${this.projectName}/${file.relativePath}`, file);
		}

		const zipBuffer = await this._createZipInMemory(needSendPaths, pathToFile);
		const zipBase64 = zipBuffer.toString("base64");

		const fileNames = files.map(f => `${this.projectPathName}/${this.projectName}/${f.relativePath}`);
		const fileHashes = files.map(f => f.hash);

		const requestData = {
			inputs: zipBase64,
			ft_token: userId,
			meta_datas: {
				file_name: `${this.projectPathName}.zip`,
				file_paths: JSON.stringify(needSendPaths),
				file_dir_names: JSON.stringify(fileNames),
				file_hash: JSON.stringify(fileHashes),
				project_name: this.projectName,
				project_path_name: this.projectPathName,
				project_id: this.projectId,
				user_id: userId,
			},
		};

		const encryptedData = encryptJSON(requestData);

		const response = await fetch(`${this.serverUrl}/sync_repo_content`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({
				encrypted: true,
				data: encryptedData,
			}),
			dispatcher: this.proxyAgent,
			signal: AbortSignal.timeout(this.timeoutMs),
		});

		if (!response.ok) {
			throw new Error(`sync_repo_content failed: HTTP ${response.status}`);
		}
	}

	private async _createZipInMemory(needSendPaths: string[], pathToFile: Map<string, FileInfo>): Promise<Buffer> {
		return new Promise((resolve, reject) => {
			const chunks: Buffer[] = [];
			const archive = archiver("zip", { zlib: { level: 9 } });

			archive.on("data", (chunk: Buffer) => {
				chunks.push(chunk);
			});

			archive.on("end", () => {
				resolve(Buffer.concat(chunks));
			});

			archive.on("error", reject);

			let fileIndex = 1;
			for (const filePath of needSendPaths) {
				if (!filePath) continue;

				const file = pathToFile.get(filePath);
				if (!file || file.isDirectory) continue;

				const dirName = String(fileIndex).padStart(8, "0");
				const fileName = path.basename(file.relativePath);
				const archivePath = `${dirName}/${fileName}`;

				archive.file(file.fullPath, { name: archivePath });
				fileIndex++;
			}

			archive.finalize();
		});
	}
}
