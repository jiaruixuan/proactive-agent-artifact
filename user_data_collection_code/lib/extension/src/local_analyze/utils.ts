import * as fs from "fs";
import ignore from "ignore";
import * as os from "os";
import * as path from "path";
import { fetch, ProxyAgent } from "undici";
import * as vscode from "vscode";
import { globalConfig } from "../globalConfig";
import { translate } from "../Translate";
import { getContext, getDisplayLanguage } from "../utils/GlobalVariables";
import { getServerURL } from "../utils/ServerURL";
import { statistic_sending_gap } from "./CompletionStatistics";

const ignore_max_cache_size = 1000000;
const max_cache_size = 10000;
const var_compress_threshold = 1000;

// class/function symbol kinds
const childrenable_symbol_kinds: vscode.SymbolKind[] = [
	vscode.SymbolKind.File,
	vscode.SymbolKind.Module,
	vscode.SymbolKind.Namespace,
	vscode.SymbolKind.Package,
	vscode.SymbolKind.Class,
	vscode.SymbolKind.Method,
	vscode.SymbolKind.Constructor,
	vscode.SymbolKind.Enum,
	vscode.SymbolKind.Interface,
	vscode.SymbolKind.Function,
	vscode.SymbolKind.Struct,
	vscode.SymbolKind.Operator,
];

// function symbol kinds
const function_symbol_kinds: vscode.SymbolKind[] = [
	vscode.SymbolKind.Method,
	vscode.SymbolKind.Constructor,
	// vscode.SymbolKind.Interface,
	vscode.SymbolKind.Function,
	vscode.SymbolKind.Operator,
];

// code block, one line command
const language_keywords: { [key: string]: string[] } = {
	python: [":", "", "#<content>"],
	c: ["{", "}", "//<content>"],
	cpp: ["{", "}", "//<content>"],
	csharp: ["{", "}", "//<content>"],
	kotlin: ["{", "}", "//<content>"],
	java: ["{", "}", "//<content>"],
	javascript: ["{", "}", "//<content>"],
	typescript: ["{", "}", "//<content>"],
	php: ["{", "}", "//<content>"],
	go: ["{", "}", "//<content>"],
	rust: ["{", "}", "//<content>"],
	ruby: ["\n", "end", "#<content>"],
	lua: ["\n", "end", "--<content>"],
	perl: ["{", "}", "#<content>"],
	css: ["{", "}", "/*<content>*/"],
	matlab: ["\n", "end", "%<content>"],
	unknown: ["{", "}", "//<content>"],
};

const languageServerExtensions: { [key: string]: string } = {
	python: "ms-python.python",
	csharp: "ms-dotnettools.csdevkit",
	java: "vscjava.vscode-java-pack",
	go: "golang.go",
	rust: "rust-lang.rust-analyzer",
	php: "DEVSENSE.phptools-vscode",
	cpp: "ms-vscode.cpptools-extension-pack",
	c: "ms-vscode.cpptools-extension-pack",
	dart: "Dart-Code.dart-code",
};

const whiteLanList: Set<string> = new Set(["plaintext", "markdown", "json", "yaml", "html", "css", "xml"]);

const notify_interval = 1000 * 60 * 60; // 1 hour
const notifyState: { [key: string]: { last_notify_time: number; dismissed_notify: boolean } } = {
	python: { last_notify_time: 0, dismissed_notify: false },
	csharp: { last_notify_time: 0, dismissed_notify: false },
	java: { last_notify_time: 0, dismissed_notify: false },
	go: { last_notify_time: 0, dismissed_notify: false },
	rust: { last_notify_time: 0, dismissed_notify: false },
	php: { last_notify_time: 0, dismissed_notify: false },
	cpp: { last_notify_time: 0, dismissed_notify: false },
	c: { last_notify_time: 0, dismissed_notify: false },
	dart: { last_notify_time: 0, dismissed_notify: false },
};

const languageConfig: { [key: string]: { code: string; suffix: string } } = {
	// python
	python: {
		code: `def test():
  print("Hello, world!")

test()
`,
		suffix: "py",
	},
	// c#
	csharp: {
		code: `using System;

public class Department
{
    public string Name { get; set; }

    public Department(string name)
    {
        Name = name;
    }
}

class Program
{
    static void Main()
    {
        Department dept = new Department("Engineering");
        Console.WriteLine(dept.Name);
    }
}`,
		suffix: "cs",
	},
	// java
	java: {
		code: `public class Department {
    private String name;

    public Department(String name) {
        this.name = name;
    }

    public static void main(String[] args) {
        Department dept = new Department("Engineering");
    }
}`,
		suffix: "java",
	},
	go: {
		code: `package main

import "fmt"

func main() {
    test()
}

func test() {
    fmt.Println("Hello, world!")
}`,
		suffix: "go",
	},
	rust: {
		code: `struct Person {
    name: String,
    age: u32,
}`,
		suffix: "rs",
	},
	php: {
		code: `<?php

class Greeting {
    public function sayHello() {
        echo "Hello, world!\n";
    }
}

$greeting = new Greeting();
$greeting->sayHello();
`,
		suffix: "php",
	},
	c: {
		code: `void test()
{
  return;
}

int main()
{
  test();
  return 0;
}
`,
		suffix: "c",
	},
	cpp: {
		code: `void test()
{
  return;
}

void main()
{
  test();
}
`,
		suffix: "cpp",
	},
	dart: {
		code: `void sayHello(String name) {
    print('Hello, $name!');
}

void main() {
    sayHello('World');
}
`,
		suffix: "dart",
	},
};

export function getLanComment(language: string) {
	if (!language_keywords.hasOwnProperty(language)) {
		return "//";
	} else {
		return language_keywords[language]![2]!.replace("<content>", "");
	}
}

async function checkHasSymbol(languageId: string): Promise<boolean> {
	const tempDir = os.tmpdir();
	const languageSuffix = languageConfig[languageId]?.suffix;
	const languageCode = languageConfig[languageId]?.code;

	if (!languageSuffix || !languageCode) {
		return false;
	}

	const tempFilePath = path.join(tempDir, `temp.${languageSuffix}`);

	try {
		await fs.promises.writeFile(tempFilePath, languageCode);

		const document = await vscode.workspace.openTextDocument(tempFilePath);
		const symbols = await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
			"vscode.executeDocumentSymbolProvider",
			document.uri
		);

		return symbols?.length > 0;
	} catch (error) {
		console.error(error);
		return false;
	} finally {
		try {
			await fs.promises.unlink(tempFilePath);
		} catch (error) {
			console.error(error);
		}
	}
}

// 1: true, 0: false, -1: unknown
export async function checkHasLSP(document: vscode.TextDocument) {
	const languageId = document.languageId;
	if (languageId == "javascript" || languageId == "typescript") return 1;
	const symbols = await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
		"vscode.executeDocumentSymbolProvider",
		document.uri
	);
	if (symbols) return 1;
	else {
		if (languageServerExtensions.hasOwnProperty(languageId)) {
			if (vscode.extensions.getExtension(languageServerExtensions[languageId]!) || (await checkHasSymbol(languageId))) return 1;
			else return 0;
		} else return -1;
	}
}

export function notifyInstallLSP(languageId: string) {
	if (!notifyState.hasOwnProperty(languageId)) {
		notifyState[languageId] = { last_notify_time: 0, dismissed_notify: false };
	}
	const state = notifyState[languageId]!;
	const extensionId = languageServerExtensions[languageId];
	const language = getDisplayLanguage();
	const neverShowAgainAction = translate("Never show again", language);
	const dismissAction = translate("Dismiss", language);
	if (extensionId) {
		if (state.dismissed_notify) return;
		if (Date.now() - state.last_notify_time < notify_interval) return;
		const installAction = translate("Go to 'Extensions' to install", language);
		state.last_notify_time = Date.now();
		vscode.window
			.showInformationMessage(
				translate(
					"The Language Server for the current language is not installed, so Entire Project Perception based Completion is temporarily unavailable",
					language
				),
				installAction,
				dismissAction
			)
			.then(async result => {
				if (result === installAction) {
					await vscode.commands.executeCommand("workbench.extensions.search", `@id:${extensionId}`);
					await vscode.commands.executeCommand("extension.open", extensionId);
					await vscode.commands.executeCommand("workbench.extensions.installExtension", extensionId);
				} else if (result === dismissAction) {
					state.dismissed_notify = true;
				}
			});
	} else if (!whiteLanList.has(languageId)) {
		const ctx = getContext();
		if (!ctx) return;
		const neverShowAgain = ctx.globalState.get(`neverShowLSPTip.${languageId}`);
		if (neverShowAgain) return;
		if (Date.now() - state.last_notify_time < notify_interval) return;
		state.last_notify_time = Date.now();
		vscode.window
			.showInformationMessage(
				translate(
					"Please install the Language Server for the current language to enable Entire Project Perception based Completion",
					language
				),
				neverShowAgainAction
			)
			.then(async result => {
				if (result === neverShowAgainAction) {
					await ctx.globalState.update(`neverShowLSPTip.${languageId}`, true);
				}
			});
	}
}

let file_ignored_cache: { [key: string]: boolean } = {};
export async function isPathIgnored(filePath: string) {
	async function isPathIgnored_(filePath: string) {
		const workspaceFolders = vscode.workspace.workspaceFolders;
		if (!workspaceFolders || workspaceFolders.length === 0) {
			return false;
		}
		const rootPath = workspaceFolders[0]?.uri.fsPath;
		if (!rootPath) return false;
		if (!filePath.startsWith(rootPath)) return false;

		const rootGitignorePath = path.join(rootPath, ".gitignore");
		if (fs.existsSync(rootGitignorePath)) {
			const ig = ignore();
			ig.add(await fs.promises.readFile(rootGitignorePath, "utf8"));
			const relativePath = path.relative(rootPath, filePath);
			if (ig.ignores(relativePath)) return true;
		}

		let currentDir = path.dirname(filePath);

		while (currentDir !== rootPath && currentDir !== path.dirname(currentDir)) {
			const gitignorePath = path.join(currentDir, ".gitignore");
			if (fs.existsSync(gitignorePath)) {
				const ig = ignore();
				const gitignoreContent = await fs.promises.readFile(gitignorePath, "utf8");
				ig.add(gitignoreContent);
				const relativePath = path.relative(currentDir, filePath);

				if (ig.ignores(relativePath)) {
					return true;
				}
			}
			currentDir = path.dirname(currentDir);
		}

		return false;
	}
	if (Object.keys(file_ignored_cache).length > ignore_max_cache_size) {
		file_ignored_cache = {};
	}
	if (file_ignored_cache.hasOwnProperty(filePath)) {
		return file_ignored_cache[filePath];
	} else {
		const res = await isPathIgnored_(filePath);
		file_ignored_cache[filePath] = res;
		return res;
	}
}

// check if is in the same project & another file.
export async function check_uri_available(project_path: string, document: vscode.TextDocument, uri: vscode.Uri) {
	return uri.path.startsWith(project_path) && uri.path != document.uri.path && !(await isPathIgnored(uri.fsPath));
}

let symbol_cache: { [key: string]: vscode.DocumentSymbol[] } = {};
let symbol_key: { [key: string]: string } = {};
async function get_symbols(document: vscode.TextDocument) {
	async function get_symbols_(document: vscode.TextDocument) {
		for (let i = 0; i < 100; i++) {
			const old_document_version = document.version;
			const symbols = await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
				"vscode.executeDocumentSymbolProvider",
				document?.uri
			);
			if (document.version == old_document_version) return symbols;
		}
		const symbols = await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
			"vscode.executeDocumentSymbolProvider",
			document?.uri
		);
		return symbols;
	}
	if (Object.keys(symbol_cache).length > max_cache_size) {
		symbol_cache = {};
		symbol_key = {};
	}
	const path = document.uri.fsPath;
	let key = document.lineCount.toString() + "_" + document.offsetAt(document.lineAt(document.lineCount - 1).range.end).toString();
	if (symbol_key.hasOwnProperty(path) && symbol_key[path] == key) {
		return symbol_cache[path]!;
	}
	const symbols = await get_symbols_(document);
	key = document.lineCount.toString() + "_" + document.offsetAt(document.lineAt(document.lineCount - 1).range.end).toString();
	symbol_cache[path] = symbols;
	symbol_key[path] = key;
	return symbols;
}

function get_indentation(line: string) {
	let i = 0;
	while (i < line.length && (line[i] == " " || line[i] == "\t")) {
		i++;
	}
	return line.substring(0, i);
}

function merge_sub_result(
	document: vscode.TextDocument,
	symbol: vscode.DocumentSymbol,
	sub_result: string,
	sub_indentation: string,
	indentation: string,
	language: string,
	need_ellipses: boolean
) {
	const keywords = language_keywords[language]!;
	const start_pos = new vscode.Position(symbol.range.start.line, 0);
	const start_offset = document.offsetAt(start_pos);
	const name_offset = document.offsetAt(symbol.selectionRange.start);

	const code = document.getText(new vscode.Range(start_pos, symbol.range.end));
	const code_block_start = code.indexOf(keywords[0]!, name_offset - start_offset);
	let res = code;
	if (code_block_start != -1) {
		let ellipses = "";
		if (need_ellipses) {
			ellipses = sub_indentation + "...\n";
		}
		res = code.substring(0, code_block_start) + keywords[0]! + "\n" + ellipses + sub_result + "\n" + indentation + keywords[1]!;
	}
	return res;
}

function get_newline_char(str: string) {
	if (str.includes("\r\n")) {
		return "\r\n";
	} else if (str.includes("\n")) {
		return "\n";
	} else if (str.includes("\r")) {
		return "\r";
	} else {
		return "\n";
	}
}

export async function get_compress_code_of_symbol(document: vscode.TextDocument, symbol_name_pos: vscode.Position) {
	const symbols = await get_symbols(document);
	const language = document.languageId;
	function dfs(now_symbols: vscode.DocumentSymbol[], in_symbol: boolean, parent_indentation: string) {
		let result = "";
		let indentation = parent_indentation + "    ";
		if (now_symbols.length > 0) {
			indentation = get_indentation(document.lineAt(now_symbols[0]!.range.start.line).text);
		}
		for (const symbol of now_symbols) {
			if (in_symbol || (symbol.range.start.line <= symbol_name_pos.line && symbol.range.end.line >= symbol_name_pos.line)) {
				let in_symbol_ = in_symbol;
				if (symbol.selectionRange.start.line == symbol_name_pos.line) {
					in_symbol_ = true;
				}
				if (childrenable_symbol_kinds.includes(symbol.kind)) {
					// is a class or function
					let sub_res = ["", indentation + "    "];
					if (!function_symbol_kinds.includes(symbol.kind)) {
						// is a class
						sub_res = dfs(symbol.children, in_symbol_, indentation);
					}
					const need_ellipses = !in_symbol_ || function_symbol_kinds.includes(symbol.kind);
					result += "\n" + merge_sub_result(document, symbol, sub_res[0]!, sub_res[1]!, indentation, language, need_ellipses);
				} else {
					// is a variable
					if (document.offsetAt(symbol.range.end) - document.offsetAt(symbol.range.start) > var_compress_threshold) {
						const sub_res = ["", indentation + "    "];
						result += "\n" + merge_sub_result(document, symbol, sub_res[0]!, sub_res[1]!, indentation, language, true);
					} else {
						// result += "\n" + document.getText(new vscode.Range(new vscode.Position(symbol.range.start.line, 0), symbol.range.end));
						result += "\n" + indentation + document.getText(symbol.range);
					}
				}
			}
		}
		return [result, indentation];
	}
	function delete_empty_lines(code: string) {
		const newline = get_newline_char(code);
		const lines = code.split(newline);
		let res = "";
		for (let i = 0; i < lines.length; i++) {
			const line = lines[i]!;
			if (line.trim() != "") {
				if (res != "") res += newline;
				res += line;
			}
		}
		return res;
	}
	const result = dfs(symbols, false, "");
	return delete_empty_lines(result[0]!);
}

// export async function get_compress_code_of_top_symbols(document: vscode.TextDocument) {
//   function delete_empty_lines(code: string) {
//     let newline = get_newline_char(code);
//     let lines = code.split(newline);
//     let res = "";
//     for (let i = 0; i < lines.length; i++) {
//       let line = lines[i]!;
//       if (line.trim() != "") {
//         if (res != "") res += newline;
//         res += line;
//       }
//     }
//     return res;
//   }
//   function dfs(
//     now_symbols: vscode.DocumentSymbol[],
//     parent_indentation: string
//   ) {
//     let result = "";
//     let indentation = parent_indentation + "    ";
//     if (now_symbols.length > 0) {
//       indentation = get_indentation(
//         document.lineAt(now_symbols[0]!.range.start.line).text
//       );
//     }
//     for (const symbol of now_symbols) {
//       if (childrenable_symbol_kinds.includes(symbol.kind)) {
//         // is a class or function
//         let sub_res = ["", indentation + "    "];
//         if (!function_symbol_kinds.includes(symbol.kind)) {
//           // is a class
//           sub_res = dfs(symbol.children, indentation);
//         }
//         let need_ellipses = function_symbol_kinds.includes(symbol.kind);
//         result +=
//           "\n" +
//           merge_sub_result(
//             document,
//             symbol,
//             sub_res[0]!,
//             sub_res[1]!,
//             indentation,
//             language,
//             need_ellipses
//           );
//       } else {
//         // is a variable
//         if (
//           document.offsetAt(symbol.range.end) -
//           document.offsetAt(symbol.range.start) >
//           var_compress_threshold
//         ) {
//           let sub_res = ["", indentation + "    "];
//           result +=
//             "\n" +
//             merge_sub_result(
//               document,
//               symbol,
//               sub_res[0]!,
//               sub_res[1]!,
//               indentation,
//               language,
//               true
//             );
//         } else {
//           // result += "\n" + document.getText(new vscode.Range(new vscode.Position(symbol.range.start.line, 0), symbol.range.end));
//           result += "\n" + indentation + document.getText(symbol.range);
//         }
//       }
//     }
//     return [result, indentation];
//   }
//   const symbols = await get_symbols(document);
//   let language = document.languageId;
//   let result = dfs(symbols, "");
//   return delete_empty_lines(result[0]!);
// }

export async function get_type_definition(document: vscode.TextDocument, position: vscode.Position) {
	const start1 = Date.now();
	const definitions1 = await vscode.commands.executeCommand<(vscode.Location | vscode.LocationLink)[]>(
		"vscode.executeTypeDefinitionProvider",
		document.uri,
		position
	);
	const start2 = Date.now();
	const definitions2 = await vscode.commands.executeCommand<(vscode.Location | vscode.LocationLink)[]>(
		"vscode.executeDefinitionProvider",
		document.uri,
		position
	);
	definitions1.push(...definitions2);
	const definitions: vscode.Location[] = [];
	for (const definition of definitions1) {
		const zeroPosition = new vscode.Position(0, 0);
		const zeroRange = new vscode.Range(zeroPosition, zeroPosition);
		if (definition instanceof vscode.Location) {
			definitions.push(definition);
		} else {
			if (definition.targetSelectionRange) {
				definitions.push(new vscode.Location(definition.targetUri, definition.targetSelectionRange));
			} else if (definition.originSelectionRange && definition.targetRange.isEqual(zeroRange)) {
				definitions.push(new vscode.Location(definition.targetUri, definition.targetRange));
			}
		}
	}
	return definitions;
}

class HeartBeater {
	heart_beat_gap: number = 30 * 1000; // ms; 30s
	ban_time: number = 60 * 60 * 1000; // ms; 1 hour
	timeout_threshold: number = 1000; // ms; 1s
	check_times: number = 10;
	timeout_limit: number = 5;

	status: number = 0; // 0: document uri not set; 1: can be choosed; 2: can not be choosed
	last_ban_time: number = 0;
	last_call_time: number = 0;
	uri: vscode.Uri | undefined;
	gaps: number[] = [];

	constructor() {
		this.last_call_time = performance.now();
		const sending_timer = setInterval(async () => {
			const base_gap = performance.now() - this.last_call_time - this.heart_beat_gap;
			this.last_call_time = performance.now();
			if (this.status == 2 && performance.now() - this.last_ban_time > this.ban_time) {
				this.gaps = [];
				this.status = 1;
			}
			if (this.status == 1) {
				const now = performance.now();
				const temp = await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
					"vscode.executeDocumentSymbolProvider",
					this.uri
				);
				if (temp) {
					const gap = performance.now() - now + base_gap;
					this.gaps.push(gap);
					if (this.gaps.length > this.check_times) {
						this.gaps.shift();
					}
					let timeout_gaps = 0;
					for (const gap of this.gaps) {
						if (gap > this.timeout_threshold) {
							timeout_gaps++;
						}
					}
					if (timeout_gaps >= this.timeout_limit) {
						this.status = 2;
						this.gaps = [];
						this.last_ban_time = performance.now();
					}
				} else {
					this.status = 0;
				}
			}
		}, this.heart_beat_gap);
	}

	update_uri(uri: vscode.Uri) {
		if (this.status === 0) {
			this.uri = uri;
			this.status = 1;
		}
	}
}
export const global_heart_beater = new HeartBeater();

export async function checkProjectCompletionAvailable(user_id: string, has_lsp: number) {
	let use_var_type_prompt = false;
	const pc_setting = vscode.workspace.getConfiguration("ideassistant.useProjectCompletion").get("open") as string;

	const heartBeatStatus = global_heart_beater.status;

	switch (pc_setting) {
		case "Auto":
			if (has_lsp === 1 && heartBeatStatus !== 2) {
				use_var_type_prompt = true;
			}
			break;
		case "On":
			if (has_lsp === 1 && heartBeatStatus !== 2) {
				use_var_type_prompt = true;
			}
			break;
		case "Off":
			use_var_type_prompt = false;
			break;
		default: {
			const config = vscode.workspace.getConfiguration("ideassistant.useProjectCompletion");
			await config.update("open", undefined, vscode.ConfigurationTarget.Global);
			if (has_lsp === 1 && heartBeatStatus !== 2) {
				use_var_type_prompt = true;
			}
		}
	}

	return use_var_type_prompt;
}

class GrayPlan {
	public name: string;
	public online_result: number | undefined; // "undefined" means need to send request to get result
	public enterprise_result: number | undefined;
	constructor(name: string, online_result: number | undefined, enterprise_result: number | undefined) {
		this.name = name;
		this.online_result = online_result;
		this.enterprise_result = enterprise_result;
	}
}

const gray_plans: GrayPlan[] = [
	// new GrayPlan("project_completion", undefined, 2), // 0: AB test off, 1: AB test on, 2: enterprise on
	// new GrayPlan("completion_type", 0, 0),
	new GrayPlan("edit_mode_auto", undefined, 1), // 0: off, 1: on
	new GrayPlan("write_mode_beta", undefined, 1),
	new GrayPlan("recent_files_mode", undefined, 1),
	new GrayPlan("agent_mode_beta", undefined, 1),
];

class GrayStatus {
	public lastUserId: string = "";
	public lastRequestTime: number = 0;
	public lastResult: number = 0;
}

export class GrayTestHelper {
	private static instance: GrayTestHelper;
	private grayStatusDict: { [key: string]: GrayStatus } = {};

	constructor() {
		for (const plan of gray_plans) {
			this.grayStatusDict[plan.name] = new GrayStatus();
		}
	}

	public static getInstance(): GrayTestHelper {
		if (!GrayTestHelper.instance) {
			GrayTestHelper.instance = new GrayTestHelper();
		}
		return GrayTestHelper.instance;
	}

	private async sendRequest(userId: string, planName: string) {
		try {
			const proxy = vscode.workspace.getConfiguration("http").get("proxy") as string;
			const proxyClient = proxy ? new ProxyAgent(proxy) : undefined;
			const serverURL = getServerURL();
			const currentExtention =
				vscode.extensions.getExtension("Anonymous.IDE-Assistant") ||
				vscode.extensions.getExtension("Anonymous.IDE-Assistant-Enterprise");
			const currentVersion = currentExtention?.packageJSON.version;
			const response = await fetch(
				serverURL +
					`/codeuser/gray_test?plan_name=${planName}&ft_token=${userId}&ide=vsc&ide_name=vscode&ide_version=${vscode.version}&extension_version=${currentVersion}`,
				{
					method: "GET",
					headers: {
						"Content-Type": "application/json",
					},
					dispatcher: proxyClient,
				}
			);

			if (!response.ok) {
				const errorInfo = await response.text();
				throw new Error(`HTTP Error: ${response.status} ${response.statusText}. Error Info: ${errorInfo}`);
			}
			const res = await response.text();
			return parseInt(res);
		} catch (error) {
			console.error("There has been a problem with your fetch operation:", error);
			return 0;
		}
	}

	private async updateResult(userId: string, planName: string) {
		const res = await this.sendRequest(userId, planName);
		const status = this.grayStatusDict[planName]!;
		status.lastUserId = userId;
		status.lastRequestTime = performance.now();
		status.lastResult = res;
	}

	public async getResult(userId: string, planName: string): Promise<number> {
		if (gray_plans.findIndex(p => p.name === planName) === -1) {
			console.error(`Gray plan ${planName} not found`);
			return 0;
		}

		// return predefined result if it is available
		const plan = gray_plans.find(p => p.name === planName)!;
		if (globalConfig.APP_VERSION != "default") {
			if (plan.enterprise_result !== undefined) {
				return plan.enterprise_result;
			}
		} else {
			if (plan.online_result !== undefined) {
				return plan.online_result;
			}
		}

		// send request to get result
		const status = this.grayStatusDict[planName]!;
		if (userId == status.lastUserId && performance.now() - status.lastRequestTime < statistic_sending_gap) {
			return status.lastResult;
		} else {
			if (userId == status.lastUserId) {
				this.updateResult(userId, planName);
				return status.lastResult;
			} else {
				await this.updateResult(userId, planName);
				return status.lastResult; // TODO: check if this changed
			}
		}
	}

	public async getAllResults(userId: string): Promise<{ [key: string]: number }> {
		const results: { [key: string]: number } = {};
		for (const plan of gray_plans) {
			results[plan.name] = await this.getResult(userId, plan.name);
		}
		return results;
	}
}
