import type { ResponseFunction } from "../chat/responseFunctions/all";
import { globalLogger } from "../extension";
import { OperationCollection } from "../operation_collection/operationCollection";
import { sanitizeString } from "../operation_collection/utils/sanitize";
import { getRemoteDefaultBranch, getRemoteGit } from "../utils/Git";
import { getPlatfomInfoAsUrlParams } from "../utils/PlatformInfoCollector";
import { getServerURL } from "../utils/ServerURL";
import { get_project_path_name } from "../utils/uploadProject";
import { McpHub } from "./mcp/mcpHub";
import { handleCodeAnalysis } from "./utils/codeDetection";
import { closeDiffViewById, saveDiffDocument, saveDiffToFile, showFileStringDiff } from "./utils/diffView";
import { deleteFile, isFileExists, listFiles, readFile, readFromTempFileOrWrite, searchFileNames } from "./utils/files";
import { getIndexProgress_ } from "./utils/getIndexProgress";
import { regexSearchFiles } from "./utils/ripgrep";
import { TerminalManager } from "./utils/terminalManager";
import { extractTextFromFile } from "./utils/textExtractor";
import { getAgentConfig, getUserId } from "./utils/utils";

export const conversationAgentWebResponseFunction: ResponseFunction[] = [
	{
		name: "getPlatfomInfoAsUrlParams",
		async func(_param) {
			return getPlatfomInfoAsUrlParams();
		},
	},
	{
		name: "getServerURL",
		async func(_param) {
			return getServerURL();
		},
	},
	{
		name: "updateAgentDisplayState",
		async func(param: any) {
			const { id, state } = param;
			const conversation = this.chatController?.getChatModel().getConversationById(id);
			if (conversation) {
				await conversation.updateAgentDisplayState(state);
			}
		},
	},
	{
		name: "getMcpToolInputSchema",
		async func(param: any) {
			const { server_name, tool_name } = param;
			const mcpHub = McpHub.getInstance();
			return mcpHub.getToolInputSchema(server_name, tool_name);
		},
	},
	{
		name: "getMcpPendingNotifications",
		async func(param) {
			const mcpHub = McpHub.getInstance();
			return mcpHub.getPendingNotifications();
		},
	},
	{
		name: "callMcpTool",
		async func(param: any) {
			const { server_name, tool_name, args } = param;
			const mcpHub = McpHub.getInstance();
			return await mcpHub.callTool(server_name, tool_name, args);
		},
	},
	{
		name: "renderTracker",
		async func(param: any) {
			const { options } = param;
			const chatPanel = this.chatController?.getChatPanel();
			if (chatPanel) {
				await chatPanel.renderTracker(options);
			}
		},
	},
	{
		name: "readFile",
		async func(param: any) {
			const { filePath } = param;
			return await readFile(filePath);
		},
	},
	{
		name: "readFromTempFileOrWrite",
		async func(param: any) {
			const { absPath, originalContent } = param;
			return readFromTempFileOrWrite(absPath, originalContent);
		},
	},
	{
		name: "showFileStringDiff",
		async func(param: any) {
			const { relPath, originalContent, newContent } = param;
			return await showFileStringDiff(relPath, originalContent, newContent);
		},
	},
	{
		name: "closeDiffViewById",
		async func(param: any) {
			const { diffUriStr } = param;
			await closeDiffViewById(diffUriStr);
		},
	},
	{
		name: "saveDiffToFile",
		async func(param: any) {
			const { item, num } = param;
			await saveDiffToFile(item, num);
		},
	},
	{
		name: "handleCodeAnalysis",
		async func(param: any) {
			const { diffViewUri, originalContent } = param;
			return await handleCodeAnalysis(diffViewUri, originalContent);
		},
	},
	{
		name: "saveDiffDocument",
		async func(param: any) {
			const { diffViewUriStr } = param;
			return await saveDiffDocument(diffViewUriStr);
		},
	},
	{
		name: "extractTextFromFile",
		async func(param: any) {
			const { filePath } = param;
			return await extractTextFromFile(filePath);
		},
	},
	{
		name: "listFiles",
		async func(param: any) {
			const { dirPath, recursive, limit } = param;
			return await listFiles(dirPath, recursive, limit);
		},
	},
	{
		name: "regexSearchFiles",
		async func(param: any) {
			const { cwd, absolutePath, regex, filePattern } = param;
			return await regexSearchFiles(cwd, absolutePath, regex, filePattern);
		},
	},
	{
		name: "searchFileNames",
		async func(param: any) {
			const { cwd, searchString } = param;
			return await searchFileNames(cwd, searchString);
		},
	},
	{
		name: "isTerminalAlived",
		async func(param: any) {
			const { name } = param;
			return TerminalManager.getInstance().getAlived(name);
		},
	},
	{
		name: "closeTerminal",
		async func(param: any) {
			const { name } = param;
			return TerminalManager.getInstance().closeTerminal(name);
		},
	},
	{
		name: "executeTerminalCommand",
		async func(param: any) {
			const { name, command, maxWaitMillseconds, killAfterTimeout } = param;
			return await TerminalManager.getInstance().executeCommand(name, command, maxWaitMillseconds, killAfterTimeout);
		},
	},
	{
		name: "getCommandResult",
		async func(param: any) {
			const { name, commandIdx } = param;
			return TerminalManager.getInstance().getCommandResult(name, commandIdx);
		},
	},
	{
		name: "getIndexProgress",
		async func(param: any) {
			return await getIndexProgress_();
		},
	},
	{
		name: "getUserId",
		async func(param: any) {
			return await getUserId();
		},
	},
	{
		name: "getProjectPathName",
		async func(param: any) {
			return get_project_path_name();
		},
	},
	{
		name: "isFileExists",
		async func(param: any) {
			const { filePath } = param;
			return await isFileExists(filePath);
		},
	},
	{
		name: "deleteFile",
		async func(param: any) {
			const { filePath } = param;
			return await deleteFile(filePath);
		},
	},
	{
		name: "stopAgent",
		async func(param: any) {
			const { id } = param;
			const conversation = this.chatController?.getChatModel().getConversationById(id);
			if (conversation) {
				conversation.stopAgent();
				conversation.state = { type: "userCanReply" };
				await this.chatController?.updateChatPanel();
			}
		},
	},
	{
		name: "startAgent",
		async func(param: any) {
			const { id } = param;
			const conversation = this.chatController?.getChatModel().getConversationById(id);
			if (conversation) {
				await conversation.startAgent();
				await this.chatController?.updateChatPanel();
			}
		},
	},
	{
		name: "exportAgentConversation",
		async func(params: any) {
			const { id } = params;
			await this.chatController?.getChatModel().getConversationById(id)?.exportAgentMarkdown();
		},
	},
	{
		name: "addTemplateMcpServer",
		async func(params: any) {
			const { name } = params;
			await McpHub.getInstance().addTemplateServer(name);
		},
	},
	{
		name: "restartMcpServer",
		async func(params: any) {
			await McpHub.getInstance().refreshMcpServers();
		},
	},
	{
		name: "editMcpServer",
		async func(params: any) {
			const { name } = params;
			await McpHub.getInstance().openAndLocateServer(name);
		},
	},
	{
		name: "deleteMcpServer",
		async func(params: any) {
			const { name } = params;
			await McpHub.getInstance().deleteSpecificServer(name);
		},
	},
	{
		name: "openMcpSettingFile",
		async func(params: any) {
			await McpHub.getInstance().openMcpSettingFile();
		},
	},
	{
		name: "getMcpInfo",
		async func(params: any) {
			await McpHub.getInstance().sendWebviewMcpInfo();
		},
	},
	{
		name: "toggleMcpServerDisabled",
		async func(params: any) {
			const { name, disabled } = params;
			await McpHub.getInstance().toggleServerDisabled(name, disabled);
		},
	},
	{
		name: "getAgentConfig",
		async func(params: any) {
			const { conversation } = params;
			await getAgentConfig(conversation);
		},
	},
	{
		name: "userCanReply",
		async func(param: any) {
			// const id = message.data.id;
			const { id } = param;
			const conversation = this.chatController?.getChatModel().getConversationById(id);
			if (conversation) {
				conversation.state = { type: "userCanReply" };
				await this.chatController?.updateChatPanel();
			}
		},
	},
	{
		name: "getMainBranch",
		async func(param: any) {
			getRemoteDefaultBranch();
		},
	},
	{
		name: "getRemoteGit",
		async func(param: any) {
			getRemoteGit();
		},
	},
	{
		name: "getServerToolDetails",
		async func(param: any) {
			const { server_name, tool_name } = param;
			return McpHub.getInstance().getServerToolDetails(server_name, tool_name);
		},
	},
	// ========================================================================
	// ========================================================================
	{
		name: "getAgentConfigFromId",
		async func(param) {
			const { id } = param;
			const conversationId = id.split("$")[0]!;
			const conversation = this.chatController?.getChatModel().getConversationById(conversationId);
			if (conversation) {
				return getAgentConfig(conversation);
			} else {
				return {};
			}
		},
	},
	{
		name: "updateAdaDisplayState",
		async func(param) {
			const { id, state } = param;
			const conversationId = id.split("$")[0]!;
			const conversation = this.chatController?.getChatModel().getConversationById(conversationId);
			if (conversation) {
				await conversation.updateAgentDisplayState(state);
			}
		},
	},
	{
		name: "globalLoggerAppendLine",
		async func(param) {
			const { msg } = param;
			globalLogger.appendLine(sanitizeString(String(msg)));
		},
	},
	{
		name: "operationCollectionSetAvailable",
		async func(param) {
			const { bo } = param;
			await OperationCollection.getInstance().setAvailable(bo);
		},
	},
	{
		name: "operationCollectionAddUserInput",
		async func(param) {
			const { msg } = param;
			await OperationCollection.getInstance().addUserInput(msg);
		},
	},
	{
		name: "setConversationId",
		async func(param: any) {
			const { conversationId } = param;
			const chatModel = this.chatController?.getChatModel();
			if (chatModel) {
				chatModel.selectedConversationId = conversationId;
			}
			await this.chatController?.updateChatPanel();
			await this.chatController?.showChatPanel();
		},
	},
	{
		name: "debugGetAllOperations",
		async func(param: any) {
			const { limit = 50, filterType } = param || {};
			const operations = OperationCollection.getInstance().operations;
			let result = operations.slice(-limit);
			if (filterType) {
				result = result.filter((op: any) => op.operationType === filterType);
			}
			return result.map((op: any) => ({
				id: op.id,
				operationType: op.operationType,
				role: op.role,
				timestamp: op.timestamp,
				readableDetails: op.readableDetails,
				...(op.operationType === "terminal_execution" && {
					terminalName: op.terminalName,
					terminalId: op.terminalId,
					commandLine: op.commandLine,
					output: op.output,
					exitCode: op.exitCode,
					durationMs: op.durationMs,
				}),
				...(["terminal_open", "terminal_close", "terminal_state_change"].includes(op.operationType) && {
					terminalName: op.terminalName,
					terminalId: op.terminalId,
					state: op.state,
				}),
			}));
		},
	},
	{
		name: "debugGetOperationStats",
		async func() {
			const operations = OperationCollection.getInstance().operations;
			const stats: Record<string, number> = {};
			for (const op of operations) {
				const type = (op as any).operationType || "unknown";
				stats[type] = (stats[type] || 0) + 1;
			}
			return {
				total: operations.length,
				byType: stats,
			};
		},
	},
];
