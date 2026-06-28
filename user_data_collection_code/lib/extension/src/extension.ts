import * as path from "path";
import { fetch, ProxyAgent } from "undici";
import * as vscode from "vscode";
import type { DocumentFilter } from "vscode-languageclient/node";
import { McpHub } from "./agentServerWeb/mcp/mcpHub";
import { oriViewProvider } from "./agentServerWeb/utils/diffView";
import { AIClient } from "./ai/AIClient";
import { ApiKeyManager } from "./ai/ApiKeyManager";
import { ChatController } from "./chat/ChatController";
import { ChatModel } from "./chat/ChatModel";
import { ChatPanel } from "./chat/ChatPanel";
import {
	acceptCompletion,
	type CompletionResponse,
	captureCompletionRejected,
	IDEAssistantInlineCompletionItemProvider,
	getCompletionText,
} from "./completion/CompletionItemProvider";
import { EditModeDisplayerManager, HistoryLoggerManager } from "./completion/editMode";
import { type TemplateKey, templates } from "./configTemplates";
import { Conversation } from "./conversation/Conversation";
import { ConversationTypesProvider } from "./conversation/ConversationTypesProvider";
import { getSelectedText } from "./conversation/input/getSelectedText";
import { getTerminalTextLineCount } from "./conversation/input/getTerminalText";
import { getUnitTestFramework } from "./conversation/input/getUnitTestFramework";
import { DiffEditorManager } from "./diff/DiffEditorManager";
import { globalConfig } from "./globalConfig";
import { indexRepository } from "./index/indexRepository";
import { MsgCenterExt } from "./jsonRpc/msgCenterExt";
import { showInfo } from "./local_analyze/local_analyze2";
import { registerPCListener } from "./local_analyze/ProjectCompletionListener";
import { RecentFileAnalyzer } from "./local_analyze/RecentFileAnalyze";
import { GrayTestHelper } from "./local_analyze/utils";
import { getVSCodeLogLevel, LoggerUsingVSCodeOutput } from "./logger";
import { OperationCollection } from "./operation_collection/operationCollection";
import { translate } from "./Translate";
import { getFileExtension } from "./trackerTool";
import { formatDuration } from "./utils";
import { generateCommitMessage } from "./utils/CommitMessage";
import { checkExtensionConflicts } from "./utils/DistributionChecker";
import { getDisplayLanguage, getWorkspaceRoot, setChatPanel, setContext } from "./utils/GlobalVariables";
import { migrateSettings } from "./utils/MigrateSettings";
import { getPlatfomInfoAsUrlParams } from "./utils/PlatformInfoCollector";
import { getServerURL } from "./utils/ServerURL";
import { getUploadProjectConfig, runUploadProject, UploadProject } from "./utils/uploadProject";
import { initializePostHog, PostHogProvider } from "./utils/usePostHog";
import { scheduleVersionUpdateCheck } from "./utils/VersionUpdateCheck";
import { getExtensionAll, handleConfigurationChange } from "./utils/VscodeSettings";
import { codeLensProvider, codeLensProviderEmitter } from "./vscode/codeLensProvider";
import { ContextFile } from "./vscode/contextFile";

export const globalLogger: vscode.OutputChannel = vscode.window.createOutputChannel("IDE Assistant");

let diagnoseInfo: vscode.Diagnostic;

export let language = getDisplayLanguage();

export let ctx: vscode.ExtensionContext;
export const SELECTION_CONTEXT_LINES = 5;
export const LONG_SELECTION_THRESHOLD = 2000;

function delay(ms: number) {
	return new Promise(resolve => setTimeout(resolve, ms));
}

let statusBarItem: vscode.StatusBarItem;
export function startLoading() {
	statusBarItem.text = `$(sync~spin)`;
}

export function stopLoading() {
	statusBarItem.text = `$(distro-ubuntu)`;
}

export function disable() {
	statusBarItem.text = `$(com-disable)`;
}

const activeTimeStart = JSON.parse(JSON.stringify(Date.now()));
const sessionRadomId = Date.now().toString() + Math.random().toString(36).substr(2, 9);

export function getSessionId() {
	return sessionRadomId;
}
// async function getChangelogContent(
//   context: vscode.ExtensionContext,
//   version: string
// ): Promise<string> {
//   const backgroundColor = new vscode.ThemeColor("editor.background").toString();
//   const foregroundColor = new vscode.ThemeColor("editor.foreground").toString();

//   const changelogUri = vscode.Uri.joinPath(
//     "./",
//     "CHANGELOG.md"
//   );
//   const data = await vscode.workspace.fs.readFile(changelogUri);
//   const changelogContent = new TextDecoder().decode(data);

//   const htmlContent = md.render(changelogContent);

//   return `
//       <style>
//         body {
//           margin: 0;
//           background-color: ${backgroundColor};
//           color: ${foregroundColor};
//         }
//         h1 {
//           color: ${foregroundColor};
//         }
//       </style>
//       <body>
//         <h1>IDE-Assistant extension has been updated to version ${version}</h1>
//         <div>${htmlContent}</div>
//       </body>
//     `;
// }

function notify_language(context: vscode.ExtensionContext) {
	try {
		try {
			const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone.toLowerCase();
			// console.log("get timezone: " + timezone);
			// 'asia/shanghai' 'etc/gmt-8' 'asia/hong_kong' 'asia/taipei'
			if (
				"asia/shanghai" === timezone ||
				"etc/gmt-8" === timezone ||
				"asia/hong_kong" === timezone ||
				"asia/taipei" === timezone
			) {
				// show notification
			} else {
				// do not show notification
				return;
			}
		} catch (error) {}
		const languageSelected = context.globalState.get("languageSelected");
		const config = vscode.workspace.getConfiguration("ideassistant.languagePreference");
		if (config.get("answerPreference") === "Auto") {
			return;
		}
		if (!languageSelected) {
			vscode.window
				.showInformationMessage("[IDE Assistant] Please select your preferred chat language.", "Chinese", "English")
				.then(selectedLanguage => {
					if (selectedLanguage === "English" || selectedLanguage === "Chinese") {
						// Save the selected language in the global state
						if (selectedLanguage === "English") {
							language = "en";
						} else {
							language = "zh-cn";
						}
						context.globalState.update("languageSelected", selectedLanguage);

						// Set the language configuration based on user's choice
						vscode.workspace
							.getConfiguration()
							.update("ideassistant.languagePreference.displayPreference", language, vscode.ConfigurationTarget.Global);
						vscode.workspace
							.getConfiguration()
							.update("ideassistant.languagePreference.commentPreference", language, vscode.ConfigurationTarget.Global);
					}
				});
		}
	} catch (error) {
		console.error("An error occurred while showing the language selection: ", error);
	}
}

export function getActiveTextLanguage() {
	const editor = vscode.window.activeTextEditor;
	if (editor) {
		const document = editor.document;
		return document.languageId;
	}
	return "plaintext";
}
function captureRfuncTrigger(type: string) {
	PostHogProvider.getInstance()?.capture("r_click_func_triggered", {
		session_id: getSessionId(),
		function_type: type,
		language: getActiveTextLanguage(),
	});
}

export const activate = async (context: vscode.ExtensionContext) => {
	// try {
	//   //show changelog
	//   const currentVersion = vscode.extensions.getExtension(
	//     "Anonymous.IDE-Assistant"
	//   )?.packageJSON.version;
	//   let previousVersion = context.globalState.get<string>("extensionVersion");
	//   if (previousVersion !== currentVersion) {
	//     context.globalState.update("extensionVersion", currentVersion);
	//     const result = await vscode.window.showInformationMessage(
	//       translate(
	//         "IDE-Assistant has been updated, click to view update details",
	//         language
	//       ),
	//       translate("View Updates", language)
	//     );
	//     if (result === translate("View Updates", language)) {
	//       const panel = vscode.window.createWebviewPanel(
	//         "updateNotification",
	//         "IDE-Assistant Updates", // title
	//         vscode.ViewColumn.One,
	//         {}
	//       );
	//       panel.webview.html = await getChangelogContent(context, currentVersion);
	//     }
	//   }
	// } catch (error) {
	//   console.error("An error occurred while showing the changelog: ", error);
	// }
	checkExtensionConflicts(context);
	migrateSettings();

	statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
	statusBarItem.text = `$(distro-ubuntu)`;
	statusBarItem.tooltip = translate("Show menu", language);
	statusBarItem.command = "ideassistant.showCommands";
	statusBarItem.show();
	context.subscriptions.push(statusBarItem);
	context.subscriptions.push(vscode.workspace.registerTextDocumentContentProvider("string-ori", oriViewProvider));
	const disableSpecific = vscode.workspace.getConfiguration("ideassistant.disableSpecificInlineCompletion");
	const disable_list = (disableSpecific.get("suffixes") as string).split(/;|；/).filter(item => item);
	const activeEditor = vscode.window.activeTextEditor;
	const ext = activeEditor ? path.extname(activeEditor.document.fileName).substring(1) : "";
	if (disable_list.includes(ext)) {
		disable();
	} else {
		const inlineCompletionConfig = vscode.workspace.getConfiguration("ideassistant.inlineCompletion");
		if (inlineCompletionConfig.get("enable")) {
			stopLoading();
		} else {
			disable();
		}
	}

	ctx = context;
	setContext(context);
	handleConfigTemplateChange(ctx);
	const config = vscode.workspace.getConfiguration("ideAssistant");
	const afterInsert = vscode.commands.registerCommand("llm.afterInsert", async (response: CompletionResponse) => {
		const { request_id, completions, context } = response;
		const params = {
			request_id,
			user_id: provider.user_id,
		};
		// await client.sendRequest("llm-ls/acceptCompletion", params);

		const fileSuffix = getFileExtension();
		chatPanel.renderTracker(fileSuffix, "language");

		{
			const activeEditor = vscode.window.activeTextEditor;
			const completionText = getCompletionText();
			if (activeEditor && completionText) {
				const position = activeEditor.selection.active;
				const doc = activeEditor.document;
				const startLine = Math.max(0, position.line - 5);
				const endLine = Math.min(doc.lineCount - 1, position.line + 5);
				const contextBefore = doc.getText(new vscode.Range(startLine, 0, position.line, position.character));
				const contextAfter = doc.getText(new vscode.Range(position.line, position.character, endLine + 1, 0));
				OperationCollection.getInstance().recordAcceptCompletion({
					completionText,
					filePath: doc.uri.toString(),
					line: position.line,
					contextBefore,
					contextAfter,
				});
			}
		}
		//
		acceptCompletion(params, provider.logger, ctx);
		await delay(500);

		const editor: vscode.TextEditor | undefined = vscode.window.activeTextEditor;
		if (!editor) return; // no activate editor
		const document = editor.document;
		const status = provider.completion_status_dict[document.uri.toString()];
		const this_version = document.version;
		if (status?.last_provide_version != document.version) {
			vscode.commands.executeCommand("editor.action.inlineSuggest.trigger");
			provider.logger.appendLine("Initiative trigger:" + document.version.toString());
		}
	});

	ctx.subscriptions.push(afterInsert);
	const enableCommand = vscode.commands.registerCommand("ideassistant.enable", async () => {
		const activeEditor = vscode.window.activeTextEditor;
		const fileExtension = activeEditor ? path.extname(activeEditor.document.fileName).substring(1) : "";
		const inlineCompletionConfig = vscode.workspace.getConfiguration("ideassistant.inlineCompletion");
		const currentValue = inlineCompletionConfig.get("enable");
		inlineCompletionConfig.update("enable", !currentValue, vscode.ConfigurationTarget.Global);
		// await new Promise((resolve) => setTimeout(resolve, 100));
		if (inlineCompletionConfig.get("enable") === false) {
			vscode.window.showInformationMessage(translate("Global completions are activated", language));
			const disableSpecific = vscode.workspace.getConfiguration("ideassistant.disableSpecificInlineCompletion");
			const disable_list = (disableSpecific.get("suffixes") as string).split(/;|；/).filter(item => item);
			if (disable_list.includes(fileExtension)) {
				disable();
			} else {
				stopLoading();
			}
		}
	});
	vscode.commands.registerCommand("ideassistant.customInlineSuggest.hide", async () => {
		vscode.commands.executeCommand("editor.action.inlineSuggest.hide");
		captureCompletionRejected(getCompletionText());
	});
	vscode.commands.registerCommand("ideassistant.triggerInlineSuggestionByShortcut", async () => {
		provider.isTriggeredByShortcut = true;

		const editor = vscode.window.activeTextEditor;
		let originalPosition;
		if (editor) {
			originalPosition = editor.selection.active;
		}
		await vscode.commands.executeCommand("cursorMove", {
			to: "left",
			by: "character",
			value: 0,
		});
		await vscode.commands.executeCommand("cursorMove", {
			to: "right",
			by: "character",
			value: 0,
		});
		vscode.commands.executeCommand("editor.action.inlineSuggest.trigger");
		if (editor && originalPosition) {
			editor.selection = new vscode.Selection(originalPosition, originalPosition);
		}
	});
	vscode.commands.registerCommand("ideassistant.openSettings", () => {
		vscode.commands.executeCommand("workbench.action.openSettings", "IDEAssistant");
	});
	vscode.commands.registerCommand("ideassistant.openKeyboardShortcuts", () => {
		vscode.commands.executeCommand("workbench.action.openGlobalKeybindings", "IDEAssistant");
	});
	const disableCommand = vscode.commands.registerCommand("ideassistant.disable", async () => {
		const inlineCompletionConfig = vscode.workspace.getConfiguration("ideassistant.inlineCompletion");
		const currentValue = inlineCompletionConfig.get("enable");
		inlineCompletionConfig.update("enable", !currentValue, vscode.ConfigurationTarget.Global);
		if (inlineCompletionConfig.get("enable") === true) {
			vscode.window.showInformationMessage(translate("Gloabl completions are deactivated", language));
			disable();
		}
	});
	const terminalAnalyzeCommand = vscode.commands.registerCommand("ideassistant.terminalAnalyze", async () => {
		const lineCount = await getTerminalTextLineCount();
		if (lineCount <= 500) {
			const finalLanguage = getPreferLanguage("displayPreference");
			const isAgentEnabled =
				(await PostHogProvider.getInstance()?.getFeatureEnable("agent-right-function")) && globalConfig.ENABLE_AGENT_MODE;
			await chatController.createConversation(
				`terminal-fix-${finalLanguage}`,
				undefined,
				isAgentEnabled ? "Agent" : undefined,
				isAgentEnabled ? "terminal" : undefined
			);
		} else {
			vscode.window.showInformationMessage(translate("The terminal output is too long, please re-select the text.", language));
		}
	});
	context.subscriptions.push(enableCommand);
	context.subscriptions.push(disableCommand);
	context.subscriptions.push(terminalAnalyzeCommand);
	HistoryLoggerManager.getInstance();

	vscode.commands.registerCommand("ideassistant.onlydisable", async () => {
		const activeEditor = vscode.window.activeTextEditor;
		const fileExtension = activeEditor ? path.extname(activeEditor.document.fileName).substring(1) : "";
		const disableSpecific = vscode.workspace.getConfiguration("ideassistant.disableSpecificInlineCompletion");
		const disable_list = (disableSpecific.get("suffixes") as string).split(/;|；/).filter(item => item);

		if (!disable_list.includes(fileExtension)) {
			disable_list.push(fileExtension);
			await disableSpecific.update("suffixes", disable_list.join(";"), vscode.ConfigurationTarget.Global);
			vscode.window.showInformationMessage(
				translate("Completions for files with the extensions of '", language) +
					fileExtension +
					translate("' are disabled", language)
			);
			if (disable_list.includes(fileExtension)) {
				disable();
			}
		}
	});

	vscode.commands.registerCommand("ideassistant.onlyenable", async () => {
		const activeEditor = vscode.window.activeTextEditor;
		const fileExtension = activeEditor ? path.extname(activeEditor.document.fileName).substring(1) : "";
		const fileName = vscode.window.activeTextEditor?.document.fileName;
		if (!fileName) return;
		const disableSpecific = vscode.workspace.getConfiguration("ideassistant.disableSpecificInlineCompletion");
		const inlineCompletionConfig = vscode.workspace.getConfiguration("ideassistant.inlineCompletion");
		const disable_list = (disableSpecific.get("suffixes") as string).split(/;|；/).filter(item => item);

		const index = disable_list.indexOf(fileExtension);
		if (index !== -1) {
			disable_list.splice(index, 1);
			await disableSpecific.update("suffixes", disable_list.join(";"), vscode.ConfigurationTarget.Global);
			vscode.window.showInformationMessage(
				translate("Completions for files with the extensions of '", language) +
					fileExtension +
					translate("' are enabled,global completions have been automatically activated", language)
			);
			const currentValue = inlineCompletionConfig.get("enable");
			if (!currentValue) {
				inlineCompletionConfig.update("enable", !currentValue, vscode.ConfigurationTarget.Global);
			}
			stopLoading();
		}
	});

	const apiKeyManager = new ApiKeyManager({
		secretStorage: context.secrets,
	});

	const user_id = (await apiKeyManager.getIDEAssistantUserId()) || "";
	setTimeout(async () => {
		const debugStr = vscode.workspace.getConfiguration("ideassistant").get("DocumentFile") as string;
		if (
			(await GrayTestHelper.getInstance().getResult(user_id, "recent_files_mode")) ||
			debugStr == "AnonymousDebugDocument"
		) {
			RecentFileAnalyzer.getInstance().storeProjectTree();
		}
	}, 1000);

	const provider = new IDEAssistantInlineCompletionItemProvider(context, user_id);
	const edit_mode_displayer_manager: EditModeDisplayerManager = new EditModeDisplayerManager(provider);
	const documentFilter = { pattern: "**" } as DocumentFilter | DocumentFilter[];
	vscode.languages.registerInlineCompletionItemProvider(documentFilter, provider);
	getExtensionAll(provider.user_id);
	vscode.workspace.onDidChangeConfiguration(() => handleConfigurationChange(provider.user_id));

	function insertText(text: string) {
		const editor = vscode.window.activeTextEditor;
		if (editor) {
			const selection = editor.selection;
			editor.edit(editBuilder => {
				if (selection.isEmpty) {
					editBuilder.insert(selection.start, text);
				} else {
					editBuilder.replace(selection, text);
				}
			});
		}
		feedback(text, "insert").then(() => {});
	}
	async function feedback(text: string, feedback_type: string) {
		const userId = provider.user_id;
		const serverURL = getServerURL();
		const response = await fetch(`${serverURL}/codeapi/chat/feedback?ft_token=${userId}&${getPlatfomInfoAsUrlParams()}`, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
			},
			body: JSON.stringify({
				inputs: text,
				ft_token: userId,
				meta_datas: {
					feedback_type: feedback_type,
				},
			}),
		});
	}

	context.subscriptions.push(
		vscode.commands.registerCommand("ideassistant.getDiagnoseInfo", () => {
			return diagnoseInfo;
		})
	);

	context.subscriptions.push(
		vscode.commands.registerCommand("ideassistant.errorQuickFix", async (diagnostic: vscode.Diagnostic) => {
			diagnoseInfo = diagnostic;
			const finalLanguage = getPreferLanguage("displayPreference");
			const isAgentEnabled =
				(await PostHogProvider.getInstance()?.getFeatureEnable("agent-right-function")) && globalConfig.ENABLE_AGENT_MODE;
			await chatController.createConversation(
				`diagnose-errors-${finalLanguage}`,
				undefined,
				isAgentEnabled ? "Agent" : undefined,
				isAgentEnabled ? "diagnostic" : undefined
			);
		})
	);

	context.subscriptions.push(
		vscode.languages.registerCodeActionsProvider(
			"*",
			{
				provideCodeActions: (document, range, context, token) => {
					const diagnostics = context.diagnostics;
					const actions: vscode.CodeAction[] = [];

					diagnostics.forEach(diagnostic => {
						const message = diagnostic.message.length > 20 ? diagnostic.message.substring(0, 20) + "..." : diagnostic.message;
						const action = new vscode.CodeAction(`Assistant Fix : ${message}`, vscode.CodeActionKind.QuickFix);
						action.diagnostics = [];
						action.command = {
							command: "ideassistant.errorQuickFix",
							title: "Assistant Fix Error",
							arguments: [diagnostic],
						};
						action.isPreferred = false;

						actions.push(action);
					});

					return actions;
				},
			},
			{ providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] }
		)
	);

	const commitMessage = vscode.commands.registerCommand("ideassistant.commitMessage", async e => {
		const userId = provider.user_id;
		await generateCommitMessage(userId as string, language, e);
	});

	context.subscriptions.push(commitMessage);


	const mainOutputChannel = provider.logger;
	const indexOutputChannel = provider.logger;

	const vscodeLogger = new LoggerUsingVSCodeOutput({
		outputChannel: mainOutputChannel,
		level: getVSCodeLogLevel(),
	});

	vscode.workspace.onDidChangeConfiguration(event => {
		if (event.affectsConfiguration("ideassistant.logger.level")) {
			vscodeLogger.setLevel(getVSCodeLogLevel());
		}
		if (event.affectsConfiguration("ideassistant.useAutoUploadProject.open")) {
			if (getUploadProjectConfig() !== "Off") runUploadProject(ai, context);
		}
	});

	vscode.window.onDidChangeActiveTextEditor(editor => {
		const inlineCompletionConfig = vscode.workspace.getConfiguration("ideassistant.inlineCompletion");
		const disableSpecific = vscode.workspace.getConfiguration("ideassistant.disableSpecificInlineCompletion");
		const disable_list = (disableSpecific.get("suffixes") as string).split(/;|；/).filter(item => item);
		if (editor) {
			const fileExtension = path.extname(editor.document.fileName).substring(1);
			if (disable_list.includes(fileExtension)) {
				disable();
			} else {
				if (inlineCompletionConfig.get("enable")) {
					stopLoading();
				} else {
					disable();
				}
			}
		}
	});

	let selection_timeid: NodeJS.Timeout | undefined;
	vscode.window.onDidChangeTextEditorSelection(event => {
		/**
      * Selection changed due to typing in the editor.
        Keyboard = 1,
      * Selection change due to clicking in the editor.
        Mouse = 2,
      * Selection changed because a command ran.
        Command = 3
    */
		// if (event.kind === undefined) {
		//   return;
		// }
		codeLensProviderEmitter.fire();
		// if (selection_timeid) {
		//   clearTimeout(selection_timeid);
		// }
		// selection_timeid = setTimeout(() => {
		//   chatController.updateChatPanel();
		// }, 300);
	});

	setTimeout(async () => {
		if (await apiKeyManager.needAuthLogin()) {
			const data = await apiKeyManager.authData();
			if (data) {
				await apiKeyManager.enterAuthInfo(data.access_token, data.refresh_token, data.user_info.user_id);
				await provider.retrieve_and_set_user_id();
				provider.retrieve_and_set_access_token();
				provider.retrieve_and_set_refresh_token();
			}
		}
	}, 1000);
	const [hasIDEAssistantAccessToken, hasIDEAssistantRefreshToken, hasIDEAssistantUserId, ideassistantAccessToken, ideassistantRefreshToken, ideassistantUserId] =
		await Promise.all([
			apiKeyManager.hasIDEAssistantAccessToken(),
			apiKeyManager.hasIDEAssistantRefreshToken(),
			apiKeyManager.hasIDEAssistantUserId(),
			apiKeyManager.getIDEAssistantAccessToken(),
			apiKeyManager.getIDEAssistantRefreshToken(),
			apiKeyManager.getIDEAssistantUserId(),
		]);
	const chatPanel = new ChatPanel({
		extensionUri: context.extensionUri,
		apiKeyManager,
		hasIDEAssistantRefreshToken,
		hasIDEAssistantUserId,
		hasIDEAssistantAccessToken,
		ideassistantAccessToken,
		ideassistantUserId,
		ideassistantRefreshToken,
		context,
		logger: provider.logger,
	});
	setChatPanel(chatPanel);

	const operationCollection = OperationCollection.getInstance();
	if (ideassistantUserId) {
		operationCollection.setUserId(ideassistantUserId);
	}

	apiKeyManager.onUpdate(async () => {
		const newUserId = await apiKeyManager.getIDEAssistantUserId();
		if (newUserId) {
			operationCollection.setUserId(newUserId);
		}
	});

	const chatModel = new ChatModel();

	const conversationTypesProvider = new ConversationTypesProvider({
		extensionUri: context.extensionUri,
	});

	await conversationTypesProvider.loadConversationTypes();

	const ai = new AIClient({
		apiKeyManager,
		logger: vscodeLogger,
	});

	const chatController = new ChatController({
		chatPanel,
		chatModel,
		ai,
		diffEditorManager: new DiffEditorManager({
			extensionUri: context.extensionUri,
		}),
		getConversationType(id: string) {
			return conversationTypesProvider.getConversationType(id);
		},
		basicChatTemplateId: `chat-${getPreferLanguage("displayPreference")}`,
	});
	MsgCenterExt.getInstance(chatController);
	chatPanel.onWebviewContainerReady = () => {
		MsgCenterExt.getInstance().initRpcMessageListener();
	};
	chatController.set_logger(provider.logger);

	try {
		// dump state into chatModel, restore history
		const s = chatPanel.state as any;
		// console.log("restore state", s);
		chatModel.selectedConversationId = s.selectedConversationId;
		const updateChatPanel = chatController.updateChatPanel.bind(chatController);
		const diffEditorManager = chatController.diffEditorManager;
		const logger = provider.logger;
		const finalLanguage = getPreferLanguage("displayPreference");
		for (const conversation of s.conversations) {
			if (conversation.content.type != "messageExchange") continue;
			if (conversation.content.messages.length == 0 && !(conversation.mode === "Agent")) continue;
			// console.log('add conversation to model', conversation);
			const templateId = conversation.templateId || `chat-${finalLanguage}`;
			const ctype = conversationTypesProvider.getConversationType(templateId);
			if (!ctype) continue;
			const c = new Conversation({
				id: conversation.id,
				templateId: conversation.templateId,
				initVariables: conversation.initVariables ?? {},
				ai: ai,
				updateChatPanel,
				template: ctype.template,
				diffEditorManager,
				diffData: undefined,
				logger: logger,
				chatController: chatController,
			});
			c.creationTimestamp = conversation.timestamp;
			if (conversation.isFavorited) c.setIsfavorited();
			c.state = conversation.content.state;
			c.reference = conversation.reference;
			c.error = conversation.content.error;
			if (conversation?.header?.isTitleMessage) {
				c.messages.push({
					author: "user",
					content: conversation.header.title,
					chatRequest: {
						chatContext: conversation.header.chatRequest?.chatContext ?? conversation.header.chatContext ?? undefined,
						configs: conversation.header.chatRequest?.configs ?? undefined,
					},
				});
				c.titleChatRequest = conversation.header.chatRequest ?? undefined;
			}
			c.messages.push(...conversation.content.messages);
			c.chatContextIndex = conversation.chatContextIndex;
			c.promptMessages = conversation.content.promptMessages ?? [...c.messages];
			c.mode = conversation.mode;
			c.workAreaList = conversation.workAreaList ?? [];
			if (c.mode === "Agent") {
				c.loadAgentTask(conversation.agentSerializedData);
			}
			chatModel.conversations.push(c);
		}
	} catch (e) {
		console.error("restore state error", e);
	}

	function getPreferLanguage(type: string) {
		const config = vscode.workspace.getConfiguration("ideassistant.languagePreference");
		const preference = config.get(type);
		return preference === "Auto" ? language : preference;
	}

	context.subscriptions.push(
		vscode.commands.registerCommand("ideassistant.startloading", () => {
			startLoading();
		}),
		vscode.commands.registerCommand("ideassistant.stoploading", () => {
			stopLoading();
		}),
		vscode.commands.registerCommand("ideassistant.showCommands", async () => {
			const activeEditor = vscode.window.activeTextEditor;
			const fileExtension = activeEditor ? path.extname(activeEditor.document.fileName).substring(1) : "";
			const inlineCompletionConfig = vscode.workspace.getConfiguration("ideassistant.inlineCompletion");
			const globalEnable = inlineCompletionConfig.get("enable");
			const disableSpecific = vscode.workspace.getConfiguration("ideassistant.disableSpecificInlineCompletion");
			const disable_list = (disableSpecific.get("suffixes") as string).split(/;|；/).filter(item => item);
			const commands = [];
			if (globalEnable) {
				commands.push({
					label: translate("Disable global completions", language),
					command: "ideassistant.disable",
				});
			} else {
				commands.push({
					label: translate("Enable global completions", language),
					command: "ideassistant.enable",
				});
			}

			if (fileExtension) {
				if (disable_list.includes(fileExtension)) {
					commands.push({
						label: translate(`Enable completions for files with the extensions of '${fileExtension}'`, language),
						command: "ideassistant.onlyenable",
					});
				} else {
					commands.push({
						label: translate(`Disable completions for files with the extensions of '${fileExtension}'`, language),
						command: "ideassistant.onlydisable",
					});
				}
			}
			commands.push({
				label: translate("Open IDE Assistant settings", language),
				command: "ideassistant.openSettings",
				arguments: ["IDEAssistant"],
			});
			commands.push({
				label: translate("Open Keyboard Shortcuts", language),
				command: "ideassistant.openKeyboardShortcuts",
				arguments: ["IDEAssistant"],
			});
			const selectedCommand = await vscode.window.showQuickPick(commands, {
				placeHolder: translate("", language),
			});

			if (selectedCommand && selectedCommand.command) {
				vscode.commands.executeCommand(selectedCommand.command);
			}
		}),

		vscode.window.registerWebviewViewProvider("ideassistant.chat", chatPanel, {
			webviewOptions: {
				retainContextWhenHidden: true,
			},
		}),

		vscode.commands.registerCommand("ideassistant.feedbackCopy", text => {
			// eslint-disable-next-line @typescript-eslint/no-empty-function
			feedback(text, "copy").then(() => {});
		}),
		vscode.commands.registerCommand("ideassistant.insertText", text => {
			insertText(text);
		}),
		vscode.commands.registerCommand("ideassistant.set_completion_access_token", () => {
			provider.retrieve_and_set_access_token();
		}),
		vscode.commands.registerCommand("ideassistant.set_completion_refresh_token", () => {
			provider.retrieve_and_set_refresh_token();
		}),
		vscode.commands.registerCommand("ideassistant.set_completion_user_id", () => {
			provider.retrieve_and_set_user_id();
		}),
		vscode.commands.registerCommand("ideassistant.clear_access", () => {
			provider.clear_access_token();
		}),
		vscode.commands.registerCommand("ideassistant.clear_refresh", () => {
			provider.clear_refresh_token();
		}),
		vscode.commands.registerCommand("ideassistant.clear_user_id", () => {
			provider.clear_user_id();
		}),
		vscode.commands.registerCommand(
			"ideassistant.enterIDEAssistantAccessToken",
			apiKeyManager.enterIDEAssistantAccessToken.bind(apiKeyManager)
		),
		vscode.commands.registerCommand(
			"ideassistant.enterIDEAssistantRefreshToken",
			apiKeyManager.enterIDEAssistantRefreshToken.bind(apiKeyManager)
		),
		vscode.commands.registerCommand("ideassistant.enterIDEAssistantUserId", apiKeyManager.enterIDEAssistantUserId.bind(apiKeyManager)),
		vscode.commands.registerCommand("ideassistant.clearIDEAssistantAccessToken", async () => {
			await apiKeyManager.clearIDEAssistantAccessToken();
			vscode.window.showInformationMessage("IDEAssistant AccessToken cleared.");
		}),

		vscode.commands.registerCommand("ideassistant.clearIDEAssistantRefreshToken", async () => {
			await apiKeyManager.clearIDEAssistantRefreshToken();
			vscode.window.showInformationMessage("IDEAssistant RefreshToken cleared.");
		}),

		vscode.commands.registerCommand("ideassistant.clearIDEAssistantUserId", async () => {
			await apiKeyManager.clearIDEAssistantUserId();
			vscode.window.showInformationMessage("IDEAssistant UserId cleared.");
		}),

		vscode.commands.registerCommand(
			"ideassistant.startConversation",
			async templateId => await chatController.createConversation(templateId)
		),

		vscode.commands.registerCommand("ideassistant.diagnoseErrors", async () => {
			await chatController.createConversation("diagnose-errors");
		}),
		vscode.commands.registerCommand("ideassistant.explainCode", async () => {
			captureRfuncTrigger("explain_code");
			const finalLanguage = getPreferLanguage("displayPreference");
			chatPanel.renderPanelForHistory(false);
			chatPanel.renderPanelForKnowledgeBase(false);
			chatPanel.renderTracker(`explain-code-${finalLanguage}`);
			const isAgentEnabled =
				(await PostHogProvider.getInstance()?.getFeatureEnable("agent-right-function")) && globalConfig.ENABLE_AGENT_MODE;
			await chatController.createConversation(
				`explain-code-${finalLanguage}`,
				true,
				isAgentEnabled ? "Agent" : undefined,
				isAgentEnabled ? "selection" : undefined
			);
		}),
		vscode.commands.registerCommand("ideassistant.generateProjectRules", async () => {
			const finalLanguage = getPreferLanguage("displayPreference");
			chatPanel.renderPanelForHistory(false);
			chatPanel.renderPanelForKnowledgeBase(false);
			chatPanel.renderTracker(`generate-project-rules-${finalLanguage}`);
			await chatController.createConversation(`generate-project-rules-${finalLanguage}`, true, "Agent", "selection");
		}),
		vscode.commands.registerCommand("ideassistant.optimizeCode", async () => {
			captureRfuncTrigger("optimize_code");
			const finalLanguage = getPreferLanguage("displayPreference");
			chatPanel.renderTracker(`optimize-code-${finalLanguage}`);
			const isAgentEnabled =
				(await PostHogProvider.getInstance()?.getFeatureEnable("agent-right-function")) && globalConfig.ENABLE_AGENT_MODE;
			await chatController.createConversation(
				`optimize-code-${finalLanguage}`,
				true,
				isAgentEnabled ? "Agent" : undefined,
				isAgentEnabled ? "selection" : undefined
			);
		}),
		vscode.commands.registerCommand("ideassistant.findBugs", async () => {
			captureRfuncTrigger("find_bugs");
			const finalLanguage = getPreferLanguage("displayPreference");
			chatPanel.renderPanelForHistory(false);
			chatPanel.renderPanelForKnowledgeBase(false);
			chatPanel.renderTracker(`find-bugs-${finalLanguage}`);
			const isAgentEnabled =
				(await PostHogProvider.getInstance()?.getFeatureEnable("agent-right-function")) && globalConfig.ENABLE_AGENT_MODE;
			await chatController.createConversation(
				`find-bugs-${finalLanguage}`,
				true,
				isAgentEnabled ? "Agent" : undefined,
				isAgentEnabled ? "selection" : undefined
			);
		}),
		vscode.commands.registerCommand("ideassistant.generateUnitTest", async () => {
			captureRfuncTrigger("generate_unit_test");
			chatPanel.renderPanelForHistory(false);
			chatPanel.renderPanelForKnowledgeBase(false);
			const hasUserSetUnitTestFramework = context.globalState.get("hasUserSetUnitTestFramework");
			if (!hasUserSetUnitTestFramework) {
				const curFramework = await getUnitTestFramework();
				if (curFramework === "Not specified") {
					vscode.window
						.showInformationMessage(
							translate(
								"You can go to the settings interface to customize the unit test framework for a certain language for better generation results",
								language
							),
							translate("Open settings", language)
						)
						.then(selection => {
							if (selection === translate("Open settings", language)) {
								vscode.commands.executeCommand("workbench.action.openSettings", "IDEAssistant");
							}
						});
					context.globalState.update("hasUserSetUnitTestFramework", true);
				}
			}
			const finalLanguage = getPreferLanguage("displayPreference");
			chatPanel.renderTracker(`generate-unit-test-${finalLanguage}`);
			const isAgentEnabled =
				(await PostHogProvider.getInstance()?.getFeatureEnable("agent-right-function")) && globalConfig.ENABLE_AGENT_MODE;
			await chatController.createConversation(
				`generate-unit-test-${finalLanguage}`,
				true,
				isAgentEnabled ? "Agent" : undefined,
				isAgentEnabled ? "selection" : undefined
			);
		}),
		vscode.commands.registerCommand("ideassistant.documentCode", async () => {
			captureRfuncTrigger("document_code");
			const finalLanguage = getPreferLanguage("commentPreference");
			chatPanel.renderPanelForHistory(false);
			chatPanel.renderPanelForKnowledgeBase(false);
			chatPanel.renderTracker(`document-code-${finalLanguage}`);
			const isAgentEnabled =
				(await PostHogProvider.getInstance()?.getFeatureEnable("agent-right-function")) && globalConfig.ENABLE_AGENT_MODE;
			await chatController.createConversation(
				`document-code-${finalLanguage}`,
				true,
				isAgentEnabled ? "Agent" : undefined,
				isAgentEnabled ? "selection" : undefined
			);
		}),
		vscode.commands.registerCommand("ideassistant.startChat", async () => {
			const finalLanguage = getPreferLanguage("displayPreference");
			chatPanel.renderPanelForHistory(false);
			chatPanel.renderPanelForKnowledgeBase(false);
			chatPanel.renderTracker(`chat-${finalLanguage}`);
			await chatController.createConversation(`chat-${finalLanguage}`);
		}),
		vscode.commands.registerCommand("ideassistant.titleStartChat", async () => {
			PostHogProvider.getInstance()?.capture("title_start_chat");
			const finalLanguage = getPreferLanguage("displayPreference");
			chatPanel.renderPanelForHistory(false);
			chatPanel.renderPanelForKnowledgeBase(false);
			chatPanel.renderTracker();
			await chatController.createConversation(`title-chat-${finalLanguage}`);
		}),

		vscode.commands.registerCommand("ideassistant.uploadProject", async ({ client, userId, isLoop }) => {
			try {
				const uploadProject = new UploadProject({ id: userId, client, webview: chatPanel.webviewPanel });
				await uploadProject.getProjectFiles(isLoop);
			} catch (e) {
				console.error("ideassistant: index creation error: " + e);
			}
		}),

		vscode.commands.registerCommand("ideassistant.startCustomChat", async () => {
			chatPanel.renderPanelForHistory(false);
			chatPanel.renderPanelForKnowledgeBase(false);
			const items = conversationTypesProvider.getConversationTypes().map(conversationType => ({
				id: conversationType.id,
				label: conversationType.label,
				description: (() => {
					const tags = conversationType.tags;
					return tags == null ? conversationType.source : `${conversationType.source}, ${tags.join(", ")}`;
				})(),
				detail: conversationType.description,
			}));

			const result = await vscode.window.showQuickPick(items, {
				title: `Start Custom Chat…`,
				matchOnDescription: true,
				matchOnDetail: true,
				placeHolder: "Select conversation type…",
			});

			if (result == undefined) {
				return; // user cancelled
			}

			await chatController.createConversation(result.id);
		}),
		vscode.commands.registerCommand("ideassistant.touchBar.startChat", async () => {
			chatPanel.renderTracker();
			const finalLanguage = getPreferLanguage("displayPreference");
			chatPanel.renderPanelForHistory(false);
			chatPanel.renderPanelForKnowledgeBase(false);
			await chatController.createConversation(`chat-${finalLanguage}`);
		}),
		vscode.commands.registerCommand("ideassistant.showChatPanel", async () => {
			await chatController.showChatPanel();
		}),
		vscode.commands.registerCommand(
			// "ideassistant.getStarted", async () => {
			// await vscode.commands.executeCommand("workbench.action.openWalkthrough", {
			//   category: `ideassistant.ideassistant-vscode#ideassistant`,
			// });
			"ideassistant.getStarted",
			async () => {
				await apiKeyManager.clearIDEAssistantAIApiKey();
				await apiKeyManager.clearIDEAssistantAccessToken();
				await apiKeyManager.clearIDEAssistantRefreshToken();
				await apiKeyManager.clearIDEAssistantUserId();
				vscode.window.showInformationMessage("logout successfully.");
			}
		),
		vscode.commands.registerCommand("ideassistant.tokenExpired", async (msg: string) => {
			await apiKeyManager.clearIDEAssistantAIApiKey();
			await apiKeyManager.clearIDEAssistantAccessToken();
			await apiKeyManager.clearIDEAssistantRefreshToken();
			await apiKeyManager.clearIDEAssistantUserId();
			vscode.window.showInformationMessage(
				translate(msg, language)
			);
		}),
		vscode.commands.registerCommand("ideassistant.question", async () => {
			let timeZone = "";
			try {
				// Check the current system time zone
				timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone.toLowerCase();
			} catch (e) {
				console.error(e);
			}
			// console.log(`Current time zone: ${timeZone}`);
			if (
				timeZone.includes("shanghai") ||
				timeZone.includes("gmt-8") ||
				timeZone.includes("hong_kong") ||
				language.includes("zh")
			) {
				vscode.env.openExternal(
					vscode.Uri.parse(
						// If time zone in China, open QR code
						"https://assistant.example.com/assets/images/community-qr.jpg"
					)
				);
			} else {
				vscode.env.openExternal(
					vscode.Uri.parse(
						// else open discord
						"https://community.example.com/ide-assistant"
						// "https://assistant.example.com/assets/images/community-qr-en.jpg"
					)
				);
			}
		}),
		vscode.commands.registerCommand("ideassistant.guide", async () => {
			const url = "https://docs.example.com/ide-assistant";
			// if (!language.includes("zh")) {
			// }
			vscode.env.openExternal(vscode.Uri.parse(url));
		}),
		vscode.commands.registerCommand("ideassistant.userCenter", async () => {
			chatPanel.renderPanelForUserCenter(true, context.globalState.get("Tracker"));
		}),
		vscode.commands.registerCommand("ideassistant.reloadTemplates", async () => {
			await conversationTypesProvider.loadConversationTypes();
			vscode.window.showInformationMessage("ideassistant templates reloaded.");
		}),

		vscode.commands.registerCommand("ideassistant.showLogs", () => {
			mainOutputChannel.show(true);
		}),
		vscode.commands.registerCommand("ideassistant.indexRepository", () => {
			indexRepository({
				ai: ai,
				outputChannel: indexOutputChannel,
			});
		}),
		vscode.commands.registerCommand("ideassistant.showHistory", () => {
			MsgCenterExt.send("gotoHistory");
		}),
		vscode.commands.registerCommand("ideassistant.refresh", () => {
			chatPanel.refreshWebview();
		}),
		vscode.commands.registerCommand("ideassistant.addSelectionContextToInput", async () => {
			captureRfuncTrigger("add_to_chat");
			const editor = vscode.window.activeTextEditor;
			if (editor) {
				const selectionRange = editor.selection;

				const cwd = getWorkspaceRoot();
				const fileName = path.basename(editor.document.uri.fsPath);
				const filePath = path.relative(cwd, editor.document.uri.fsPath);
				const fileUriString = editor.document.uri.toString();
				if (selectionRange.isEmpty) {
					const document = editor.document;
					const file = {
						type: "file",
						fileName: fileName,
						fileSize: document.lineCount,
						filePath: filePath,
						fileUriString: fileUriString,
					};
					await chatPanel.sendFileToWebView(file);
					return;
				}
				const selectedRange = {
					start: {
						line: selectionRange.start.line,
						character: selectionRange.start.character,
					},
					end: {
						line: selectionRange.end.line,
						character: selectionRange.end.character,
					},
				};
				const contextRange = {
					start: {
						line: Math.max(0, selectedRange.start.line - SELECTION_CONTEXT_LINES),
						character: 0,
					},
					end: {
						line: Math.min(editor.document.lineCount - 1, selectedRange.end.line + SELECTION_CONTEXT_LINES),
						character: editor.document.lineAt(
							Math.min(editor.document.lineCount - 1, selectedRange.end.line + SELECTION_CONTEXT_LINES)
						).range.end.character,
					},
				};
				const selectedText = editor.document.getText(selectionRange);
				let contextContent = editor.document.getText(
					new vscode.Range(
						new vscode.Position(contextRange.start.line, contextRange.start.character),
						new vscode.Position(contextRange.end.line, contextRange.end.character)
					)
				);
				if (selectedText.length > LONG_SELECTION_THRESHOLD) {
					contextContent = contextContent.replace(selectedText, "<omitted_selected_text>");
				}
				await vscode.commands.executeCommand("ideassistant.chat.focus");
				chatPanel.sendContextSelectionToWebview({
					fileName,
					filePath,
					fileUriString,
					selectedRange,
					selectedText,
					contextRange,
					contextContent,
				});
			}
		}),
		vscode.commands.registerCommand("ideassistant.hideSelectionCodeLens", async () => {
			const setting = vscode.workspace.getConfiguration("ideassistant");
			await setting.update("selection.showCodeLens", false, vscode.ConfigurationTarget.Global);
			codeLensProviderEmitter.fire();
		}),
		codeLensProvider,
		vscode.commands.registerCommand("ideassistant.moreActions", () => {
			MsgCenterExt.send("updateMorePanelState");
		})
		// vscode.commands.registerCommand("ideassistant.showUserCenter", () => {
		//   MsgCenterExt.send("showUserCenter");
		// }),
	);
	await vscode.commands.executeCommand("ideassistant.chat.focus");
	setTimeout(() => {
		vscode.commands.executeCommand("workbench.view.explorer");
	}, 200);
	const isEnterprise = globalConfig.APP_VERSION == "enterprise" ? true : false;
	if (isEnterprise) {
		const startLoadingCommand = vscode.commands.registerCommand("ideassistant.showKnowledgeBase", () => {
			chatPanel.renderPanelForHistory(false);
			chatPanel.renderPanelForKnowledgeBase(true);
		});
		context.subscriptions.push(startLoadingCommand);
		vscode.commands.executeCommand("setContext", "showStartLoadingCommand", true);
	}

	notify_language(context);

	if (globalConfig.APP_VERSION === "enterprise") {
		scheduleVersionUpdateCheck();
	}
	initializePostHog(apiKeyManager, sessionRadomId);
	runUploadProject(ai, context);

	registerPCListener(context, provider);

	ContextFile.getInstance().updateAllFiles();

	let heartbeatTimer: NodeJS.Timeout;
	heartbeatTimer = setInterval(
		async () => {
			try {
				const sessionDuration = formatDuration(Date.now() - activeTimeStart);
				PostHogProvider.getInstance()?.capture("session_update", {
					session_duration: sessionDuration,
					session_id: sessionRadomId,
				});
			} catch (error) {
				console.error("Error saving pending session_end:", error);
			}
		},
		1 * 60 * 1000
	);
	context.subscriptions.push({
		dispose: async () => {
			if (heartbeatTimer) {
				clearInterval(heartbeatTimer);
			}
		},
	});
	McpHub.getInstance();

	return Object.freeze({
		async registerTemplate({ template }: { template: string }) {
			conversationTypesProvider.registerExtensionTemplate({ template });
			await conversationTypesProvider.loadConversationTypes();
		},
	});
};

export const deactivate = async () => {
	// noop
	ContextFile.getInstance().dispose();
};

export function handleConfigTemplateChange(context: vscode.ExtensionContext) {
	const listener = vscode.workspace.onDidChangeConfiguration(async event => {
		if (event.affectsConfiguration("llm.configTemplate")) {
			const config = vscode.workspace.getConfiguration("ideAssistant");
			const configKey = "ideassistant/default" as TemplateKey;
			const template = templates[configKey];
			if (template) {
				const updatePromises = Object.entries(template).map(([key, val]) =>
					config.update(key, val, vscode.ConfigurationTarget.Global)
				);
				await Promise.all(updatePromises);
			}
		}
	});
	context.subscriptions.push(listener);
}

// TODO: refactor to select only highlighted code
export default async function highlightStackAttributions(): Promise<void> {
	const document = vscode.window.activeTextEditor?.document;
	if (!document) return;

	const config = vscode.workspace.getConfiguration("ideAssistant");
	const attributionWindowSize = 250;
	const attributionEndpoint = ""; //TODO: attributionEndpoint

	// get cursor postion and offset
	const cursorPosition = vscode.window.activeTextEditor?.selection.active;
	if (!cursorPosition) return;
	const cursorOffset = document.offsetAt(cursorPosition);

	const start = Math.max(0, cursorOffset - attributionWindowSize);
	const end = Math.min(document.getText().length, cursorOffset + attributionWindowSize);

	// Select the start to end span
	if (!vscode.window.activeTextEditor) return;
	vscode.window.activeTextEditor.selection = new vscode.Selection(document.positionAt(start), document.positionAt(end));
	// new Range(document.positionAt(start), document.positionAt(end));

	const text = document.getText();
	const textAroundCursor = text.slice(start, end);

	const body = { document: textAroundCursor };

	// notify user request has started
	void vscode.window.showInformationMessage("Searching for nearby code in the stack...");

	const resp = await fetch(attributionEndpoint, {
		method: "POST",
		body: JSON.stringify(body),
		headers: { "Content-Type": "application/json" },
	});

	if (!resp.ok) {
		return;
	}

	const json = (await resp.json()) as any as { spans: [number, number][] };
	const { spans } = json;

	if (spans.length === 0) {
		void vscode.window.showInformationMessage("No code found in the stack");
		return;
	}

	void vscode.window.showInformationMessage("Highlighted code was found in the stack.", "Go to stack search").then(clicked => {
		if (clicked) {
			// open stack search url in browser
			void vscode.env.openExternal(vscode.Uri.parse("https://huggingface.co/spaces/anonymous/stack-search"));
		}
	});

	// combine overlapping spans
	// const combinedSpans: [number, number][] = spans.reduce((acc, span) => {
	//   const [s, e] = span;
	//   if (acc.length === 0) return [[s, e]];
	//   const [lastStart, lastEnd] = acc[acc.length - 1];
	//   if (s <= lastEnd) {
	//     acc[acc.length - 1] = [lastStart, Math.max(lastEnd, e)];
	//   } else {
	//     acc.push([s, e]);
	//   }
	//   return acc;
	// }, [] as [number, number][]);
	const combinedSpans: [number, number][] = spans.reduce(
		(acc, span) => {
			const [s, e] = span;
			if (acc.length === 0) return [[s, e]];
			const lastSpan = acc[acc.length - 1];
			if (lastSpan) {
				const [lastStart, lastEnd] = lastSpan;
				if (s <= lastEnd) {
					acc[acc.length - 1] = [lastStart, Math.max(lastEnd, e)];
				} else {
					acc.push([s, e]);
				}
			}
			return acc;
		},
		[] as [number, number][]
	);

	const decorations = combinedSpans.map(([startChar, endChar]) => ({
		range: new vscode.Range(document.positionAt(startChar + start), document.positionAt(endChar + start)),
		hoverMessage: "This code might be in the stack!",
	}));

	// console.log("Highlighting", decorations.map(d => [d.range.start, d.range.end]));

	const decorationType = vscode.window.createTextEditorDecorationType({
		color: "red",
		textDecoration: "underline",
	});

	vscode.window.activeTextEditor?.setDecorations(decorationType, decorations);

	setTimeout(() => {
		vscode.window.activeTextEditor?.setDecorations(decorationType, []);
	}, 5000);
}
