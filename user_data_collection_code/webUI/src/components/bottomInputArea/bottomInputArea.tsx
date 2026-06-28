import ChatInputEditor from "@/components/chatInputEditor";
import { t } from "@/language";
import { MsgCenterWeb } from "@/messageCenter/msgCenterWeb";
import { useGlobalStore } from "@/store";
import { type ChatConfigs, type Context, type FileContext, type ImageContext, SelectionContext } from "@/store/interface";
import "@/styles/components/bottomInputArea.scss";
import { Modal, Select, Space, Tooltip } from "antd";
import fuzzysort from "fuzzysort";
import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { requestHttp } from "@/api";
import { CommandsList } from "@/components/CommandsList";
import { ChatInputLoadingIcon, StopIcon } from "@/components/customIcon";
import { PicturePreview } from "@/components/imgPreview";
import { ImgTag } from "@/components/imTag";
import { McpView } from "@/components/mcp/mcp";
import { Popup } from "@/components/utils/popup/popup";
import { WorkArea } from "@/components/workArea";
import { useRouteParams } from "@/router/routeUtils";
import { useIDEAssistantChatInputEditorState } from "@/store/chatInputEditorStore";
import { useChatToolsStore } from "@/store/chatToolsStore";
import { useCommandStore } from "@/store/commandStore";
import { useCommonExpressionStore } from "@/store/commonExpressionStore";
import type { Conversation } from "@/store/interface/conversation";
import { detectOS } from "@/utils";
import { cn } from "@/utils/cn";
import { getBaseVariables, getCommandParentVariable, type IBaseVariableUI, VariableContext } from "@/utils/commandPanelStrategy";
import { ImageUploadEventProvider } from "@/utils/imageUploadEvent";
import { appendImageListToChatContext, formatChatContextAndSubmitValue, updateChatContext } from "@/utils/inputEditorEvent";
import { globalTimerService } from "@/utils/util";
import { CommonExpressionFastPanel } from "../commonExpression/expressionFastPanel";
import { CommonExpressionMainPanel } from "../commonExpression/expressionMainPanel";
import { ExpressionManager } from "../commonExpression/expressionManager";
import { Mcp, Message, Picture, Ruler, SelectedSvg, SendActive, SendSvg, SvgPack } from "../ideAssistantSvg";
import { RulesSettingsPanel } from "../RulesSettingsPanel";
// const MAX_IMAGE_UPLOAD_SIZE = 350 * 1024;

export const BottomInputArea: React.FC = () => {
	const {
		setChooseCommandsVal,
		showCommand,
		setShowCommand,
		cachedCommands,
		setCachedCommands,
		addCachedCommands,
		updateCachedCommandsHideInput,
		updateOneCommandText,
		setSubCommandRouter,
		setUserInputSearchFile,
		subCommandRouter,
	} = useCommandStore();
	const params = useRouteParams();
	const isHistory = Boolean(params.isHistory);
	// i18n texts
	const privacyModalTitle = t("privacyPolicy");
	const privacyModalDisagreeText = t("disagree");
	const privacyModalAgreeText = t("agree");
	const privacyModalContent = t("privacyPolicyContent");
	const defaultModel = t("bottomInputArea.defaultLLM");
	const { pathname } = useLocation();
	const [hasShownPrivacy, setHasShownPrivacy] = useState(localStorage.getItem("hasShownPrivacypolicy") === "true");
	const { conversation, panelState, globalConfig, ideType, userId, language, setConversation } = useGlobalStore();
	const convRef = useRef(conversation);
	const { APP_VERSION = "default", CAN_CHOOSE_MODEL = true } = globalConfig;

	const location = useLocation();

	const isVSCode = ideType === "vsc";
	const CHAT_CONTEXT_LENGTH_LIMIT = 20000;
	const mode = useMemo(() => {
		if (conversation?.mode) {
			return conversation.mode.charAt(0).toUpperCase() + conversation.mode.slice(1);
		}
		return "Chat";
	}, [conversation?.mode]);

	const chatInputRef = useRef<HTMLDivElement | null>(null);

	const isP = useRef("");
	const {
		inputValue,
		setInputValue,
		setInputFocus,
		imageLabelList,
		setImageLabelList,
		getImageLabelList,
		isInputValueJustRestored,
		editorPrevChar,
	} = useIDEAssistantChatInputEditorState(state => ({
		inputValue: state.inputValue,
		setInputValue: state.setInputValue,
		setInputFocus: state.setInputFocus,
		imageLabelList: state.imageLabelList,
		setImageLabelList: state.setImageLabelList,
		getImageLabelList: state.getImageLabelList,
		isInputValueJustRestored: state.isInputValueJustRestored,
		editorPrevChar: state.editorPrevChar,
	}));

	const [pauseStatus, setPauseStatus] = useState(false);
	// UI state
	const [activeIndex, setActiveIndex] = useState(0);
	const { showPopup, setShowPopup, setChatInputHeight } = useChatToolsStore();
	const [currentImageUrl, setCurrentImageUrl] = useState<string | null>(null);
	const [currentImageText, setCurrentImageText] = useState<string | null>(null);
	const fileInputRef = useRef<HTMLInputElement>(null);
	const popupRef = useRef<HTMLDivElement | null>(null);
	const toolBoxButtonRef = useRef<HTMLDivElement | null>(null);
	const buttonRef = useRef<HTMLButtonElement | null>(null);
	const [isHover, setIsHover] = useState(false);

	const [selectImage, setSelectImage] = useState<ImageContext[]>([]);

	const [isAgentMode, setIsAgentMode] = useState(false);
	const [isRulesSettingsPanelOpen, setIsRulesSettingsPanelOpen] = useState(false);

	const [chatConfigs, setChatConfigs] = useState<ChatConfigs>({});
	const [chatContext, setChatContext] = useState<Context[]>([]);
	const chatContextRef = useRef(chatContext);

	const [contextFiles, setContextFiles] = useState<any[]>([]);
	const [recentContextFiles, setRecentContextFiles] = useState<any[]>([]);
	const [selectedCodeSegment, setSelectedCodeSegment] = useState<any>({});
	const [contextFileSelected, setContextFileSelected] = useState<FileContext[]>([]);
	const [cancelContextFile, setCancelContextFile] = useState<any>();
	const [showMcp, setShowMcp] = useState(false);
	const isInputValueJustRestoredRef = useRef<boolean>(isInputValueJustRestored);

	const {
		isAddCommonExpressionOpen,
		setIsAddCommonExpressionOpen,
		commonExpression,
		setMatchedCommonExpression,
		setCommonExpression,
	} = useCommonExpressionStore();
	// const [chooseFileList, setChooseFileList] = useState<
	// 	{ fileName: string; filePath: string; disabled?: boolean; fileContent: string; fileSize: number }[]

	const [knowledgeBaseName, setKnowledgeBaseName] = useState<any[]>([]);
	const knowledgeBaseNameRef = useRef<any[]>(knowledgeBaseName);
	const [selectedIndex, setSelectedIndex] = useState(0);

	const [isModalOpen, setIsModalOpen] = useState(false);
	const [editorClear, setEditorClear] = useState(false);
	const [chatInputType, setChatInputType] = useState("");

	const [modes, setModes] = useState<{ name: string; value: string }[]>([]);

	type ModelValue = string;
	type ServerModelMeta = {
		selection_name: string;
		display_name: string;
		is_default: boolean;
		mode: string;
		order?: number;
		is_new?: boolean;
		developer_only?: boolean;
		requires_privacy?: boolean;
		[k: string]: any;
	};
	type ServerModelsResponse = {
		chat?: ServerModelMeta[];
		agent?: ServerModelMeta[];
	};
	type Model = {
		name: string;
		value: ModelValue;
		isNew: boolean;
		isSelected: boolean;
		requiresPrivacy?: boolean;
	};
	const [persistentModel, setPersistentModel] = useState(
		window._developer ? (localStorage.getItem("persistentModel") ?? "S1") : "S1"
	);
	const [persistentAgentModel, setPersistentAgentModel] = useState(localStorage.getItem("persistentAgentModel") ?? "S5");
	const generateStaticModels = (modeFilter: "Chat" | "Agent") => {
		const chatModelList: Model[] = [
			{ name: t("bottomInputArea.defaultLLM"), value: "S1", isNew: false, isSelected: persistentModel === "S1" },
			{
				name: "DeepSeek V3",
				value: "S2",
				isNew: false,
				isSelected: persistentModel === "S2",
				requiresPrivacy: true,
			},
			{
				name: "DeepSeek R1",
				value: "S3",
				isNew: false,
				isSelected: persistentModel === "S3",
				requiresPrivacy: true,
			},
			...(window._developer
				? [
						{
							name: "Claude-Sonnet",
							value: "S4",
							isNew: false,
							isSelected: persistentModel === "S4",
						},
					]
				: []),
		];
		const agentModelList: Model[] = [
			{
				name: t("bottomInputArea.defaultLLM"),
				value: "S5",
				isNew: false,
				isSelected: persistentAgentModel === "S5",
			},
			...(window._developer
				? [
						{
							name: "Claude-Sonnet",
							value: "S6",
							isNew: false,
							isSelected: persistentAgentModel === "S6",
						},
					]
				: []),
		];

		return (modeFilter === "Chat" ? chatModelList : agentModelList) as Array<{
			name: string;
			value: ModelValue;
			isNew: boolean;
			isSelected: boolean;
			requiresPrivacy?: boolean;
		}>;
	};
	const fetchAndBuildModels = async (modeFilter: "Chat" | "Agent") => {
		const controller = new AbortController();
		const TIMEOUT_MS = 3000;
		const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);
		try {
			const res: ServerModelsResponse = await requestHttp.get("/codeapi/chat/models", { signal: controller.signal });
			const isDev = Boolean((window as any)._developer);
			const sourceList: ServerModelMeta[] = (modeFilter === "Chat" ? res.chat : res.agent) || [];
			if (sourceList.length === 0) return generateStaticModels(modeFilter);

			const filtered = sourceList.filter(m => (m.developer_only ? isDev : true));
			let currentModel = null;
			if (modeFilter == "Chat") {
				currentModel = persistentModel;
			} else {
				currentModel = persistentAgentModel;
			}
			const built = filtered.map(m => ({
				name: m.is_default ? t("bottomInputArea.defaultLLM") : m.display_name,
				value: m.selection_name as ModelValue,
				isNew: Boolean(m.is_new),
				isSelected: currentModel ? currentModel == m.selection_name : m.is_default,
				requiresPrivacy: Boolean(m.requires_privacy),
			}));
			return built;
		} catch (_e) {
			return generateStaticModels(modeFilter);
		} finally {
			clearTimeout(timeoutId);
		}
	};
	useEffect(() => {
		if (!window._developer) {
			setPersistentModel("S1");
			setPersistentAgentModel("S5");
			localStorage.removeItem("persistentModel");
			localStorage.removeItem("persistentAgentModel");
		}
	}, []);
	type AgentModelValue = string;
	const [agentModels, setAgentModels] = useState<
		Array<{ name: string; value: string; isNew: boolean; isSelected: boolean; requiresPrivacy?: boolean }>
	>(() => generateStaticModels("Agent"));
	const [selectedAgentModel, setSelectedAgentModel] = useState(agentModels.find(item => item.isSelected == true));

	const [models, setModels] = useState<
		Array<{ name: string; value: string; isNew: boolean; isSelected: boolean; requiresPrivacy?: boolean }>
	>(() => generateStaticModels("Chat"));
	const model = { name: t("bottomInputArea.defaultLLM"), value: "S1", isNew: false, isSelected: true };
	const [selectedModel, setSelectedModel] = useState(models.find(item => item.isSelected == true) ?? model);

	const [isStreaming, setIsStreaming] = useState(false);
	useEffect(() => {
		detectOS().then(res => {
			isP.current = res.platform;
		});
	}, []);
	useEffect(() => {
		(async () => {
			const chatList = await fetchAndBuildModels("Chat");
			setModels(chatList);
			const agentList = await fetchAndBuildModels("Agent");
			setAgentModels(agentList);
		})();
	}, [language, window._developer]);

	useEffect(() => {
		knowledgeBaseNameRef.current = knowledgeBaseName;
	}, [knowledgeBaseName]);


	// ---------- Helpers ----------
	const addAdditionalVariables = useCallback(() => {
		const ctx = new VariableContext(ideType, globalConfig, mode);
		const variables = ctx.buildVariables({
			cachedStates: cachedCommands,
			extendedVariables: [],
		});
		setCachedCommands(variables);
	}, [mode, ideType]);

	const openToolList = useCallback(
		(event: React.MouseEvent) => {
			event.stopPropagation();
			if (showPopup === "tool") {
				setShowPopup("nothing");
			} else {
				setShowPopup("tool");
			}
		},
		[showPopup]
	);

	const handlePictureEnlarge = useCallback((imageUrl: string, imageText: string) => {
		setCurrentImageUrl(imageUrl);
		setCurrentImageText(imageText);
		setShowPopup("picture");
	}, []);

	const handleClosePopup = useCallback(() => {
		setShowPopup("nothing");
		setCurrentImageUrl(null);
		setCurrentImageText(null);
	}, []);

	const handleCommonExpressionPopup = useCallback(() => {
		setShowPopup("common-expression");
	}, []);

	const handleRulesPopup = useCallback(() => {
		setIsRulesSettingsPanelOpen(true);
		setShowPopup("rules");
	}, []);

	const handleReportPopup = useCallback(() => {
		console.log("handleReportPopup");
	}, []);

	const getAgentChatConfigsData = useCallback(
		(modelValue?: AgentModelValue) => {
			const value = modelValue ?? (selectedAgentModel?.value as AgentModelValue);
			return { ...chatConfigs, agentModel: value === "undefined" ? undefined : value } as ChatConfigs;
		},
		[chatConfigs, selectedAgentModel?.value]
	);

	const agentModelsChange = useCallback(
		(agentModelValue: AgentModelValue) => {
			const updated = agentModels.map(m => ({ ...m, isSelected: m.value === agentModelValue }));
			setAgentModels(updated);
			const chosen = updated.find(m => m.value === agentModelValue) ?? updated[0];
			setSelectedAgentModel(chosen);

			if (chosen?.requiresPrivacy && !hasShownPrivacy) {
				setIsModalOpen(true);
			}

			setChatConfigs((prev: any) => ({ ...prev, agentModel: chosen.value as AgentModelValue }));
			setShowPopup("nothing");
		},
		[agentModels, hasShownPrivacy]
	);

	const handleImageUploadChange = useCallback(
		async (event: React.ChangeEvent<HTMLInputElement>) => {
			if (event.target.files && event.target.files.length > 0) {
				Array.from(event.target.files).forEach(file => {
					if (file) {
						uploadImage(file);
						if (fileInputRef.current) fileInputRef.current.value = "";
					}
				});
				setSelectImage([]);
			}
		},
		[imageLabelList, mode]
	);
	const handleImagePreview = (url: string, file: File) => {
		const imgItem: ImageContext = {
			id: new Date().getTime(),
			text: file.name,
			preview: url,
			status: "pending",
			name: "",
			type: "image",
		};

		setChatContext(prev => [...prev, imgItem]);
		setSelectImage(prev => [...prev, imgItem]);
		const currentList = getImageLabelList();
		setImageLabelList([...currentList, imgItem]);
		return imgItem;
	};
	const startUploadProcess = async (imgItem: ImageContext, file: File) => {
		try {
			updateImageStatus(imgItem.id, "uploading");

			const fileType = ImageUploadEventProvider.getImageType(file);
			const url = await ImageUploadEventProvider.compressAndUpload(imgItem.preview || "", fileType);
			updateImageStatus(imgItem.id, "success", url);
		} catch (e) {
			updateImageStatus(imgItem.id, "error");
			Popup.warning(t("upload.imageUploadError"));
		}
	};
	const updateImageStatus = (id: number, status: ImageContext["status"], url?: string) => {
		setChatContext(prev =>
			prev.map((item: Context) => (item.type === "image" && item.id === id ? { ...(item as ImageContext), status, url } : item))
		);
		setSelectImage(prev => prev.map(item => (item.id === id ? { ...item, status, url } : item)));
		const currentList = getImageLabelList();
		setImageLabelList(currentList.map(item => (item.id === id ? { ...item, status, url } : item)));
	};

	const uploadImage = (file: File) => {
		if (!file) return;
		const reader = new FileReader();
		reader.onloadend = async () => {
			const url = reader.result as string;
			const currentList = getImageLabelList();
			if (!ImageUploadEventProvider.uploadImageRules(mode, file.size, currentList.length)) {
				if (fileInputRef.current) fileInputRef.current.value = "";
				return;
			}
			if (mode === "Chat") {
				const fileType = ImageUploadEventProvider.getImageType(file);
				const { dataUrl } = await ImageUploadEventProvider.compressImage(url, fileType);
				const imgTagItem: ImageContext = {
					text: file.name,
					url: dataUrl,
					status: "success",
					id: new Date().getTime(),
					type: "image",
					name: "",
				};
				setChatContext([...chatContextRef.current, imgTagItem]);
				setSelectImage(prev => [...prev, imgTagItem]);
				const currentList = getImageLabelList();
				setImageLabelList([...currentList, imgTagItem]);
			} else {
				const imgItem = handleImagePreview(url, file);
				try {
					await startUploadProcess(imgItem, file);
					// const fileType = ImageUploadEventProvider.getImageType(file);
					// const { dataUrl } = await ImageUploadEventProvider.compressImage(url, fileType);
					// updateImageStatus(imgItem.id, "uploading", dataUrl);
				} catch (e) {
					Popup.warning(t("upload.imageReadFailed"));
					if (fileInputRef.current) fileInputRef.current.value = "";
				}
			}
		};
		reader.onerror = () => Popup.warning(t("upload.imageReadFailed"));
		reader.readAsDataURL(file);
	};

	const ifFileOutSizeLimit = (fileList: any[]) => {
		let totalSize = 0;
		fileList.forEach((file: { fileSize: number }) => {
			totalSize += Number(file.fileSize);
		});
		if (totalSize > 30_000) {
			Popup.warning(t("chat.resetSlectFile"));
			return false;
		}
		if (fileList.length > 10) {
			Popup.warning(t("chat.FileOutSizelimit"));
			return false;
		}
		return true;
	};

	// region Model selection & chat config
	const getChatConfigsData = useCallback(
		(modelValue?: ModelValue) => {
			const value = modelValue ?? (selectedModel?.value as ModelValue);
			return { ...chatConfigs, model: value === "undefined" ? undefined : value } as ChatConfigs;
		},
		[chatConfigs, selectedModel?.value]
	);

	const modelsChange = useCallback(
		(modelValue: ModelValue) => {
			const updated = models.map(m => ({ ...m, isSelected: m.value === modelValue }));
			setModels(updated);
			const chosen = updated.find(m => m.value === modelValue) ?? updated[0];
			setSelectedModel(chosen);

			if (chosen?.requiresPrivacy && !hasShownPrivacy) {
				setIsModalOpen(true);
			}

			setChatConfigs((prev: any) => ({ ...prev, model: chosen.value as ModelValue }));
			setShowPopup("nothing");
		},
		[models, hasShownPrivacy]
	);

	// region Context selection
	const addContextSelection = useCallback(
		(selection: any) => {
			if (!selection.filePath) return;
			const existItem = chatContextRef.current.find(
				(item): item is FileContext =>
					item.type === "selection" &&
					item?.filePath === selection.filePath &&
					item?.selectedRange.start.line === selection.selectedRange.start.line &&
					item.selectedRange.start.character === selection.selectedRange.start.character &&
					item.selectedRange.end.line === selection.selectedRange.end.line &&
					item.selectedRange.end.character === selection.selectedRange.end.character
			);
			if (existItem && existItem.refCount !== undefined) {
				existItem.refCount += 1;
				setSelectedCodeSegment({ ...existItem });
				return;
			}

			if (mode !== "Agent") {
				if (selection.selectedText.length > CHAT_CONTEXT_LENGTH_LIMIT) {
					Popup.warning(t("chat.fileSelectionLimit") + CHAT_CONTEXT_LENGTH_LIMIT);
					return;
				}
				let totalSize = 0;
				chatContextRef.current.forEach(context => {
					if (context.type === "selection") totalSize += context?.selectedText?.length ?? 0;
					else if (context.type === "file") totalSize += context?.fileSize ?? 0;
					else if (context.type === "diagnostic") totalSize += context?.contextContent?.length ?? 0;
				});
				totalSize += selection.selectedText.length;
				if (totalSize > CHAT_CONTEXT_LENGTH_LIMIT) {
					Popup.warning(t("chat.codeSelectionLimit") + CHAT_CONTEXT_LENGTH_LIMIT);
					return;
				}
			}
			const withRefNameSelection = { ...selection, refCount: 1, type: "selection" };
			setChatContext(prev => [...prev, withRefNameSelection]);
			setSelectedCodeSegment(withRefNameSelection);
		},
		[mode]
	);

	const jaUpdataIndex = useCallback(
		(param: any) => {
			MsgCenterWeb.send("updateChatContextIndex", { id: param.id, index: param.index + 1 });
		},
		[conversation?.id]
	);

	const removeFileTag = useCallback((id: string) => {
		const existItem = chatContextRef.current.find(item => (item as any).refName === id) as any;
		if (existItem && existItem.refCount !== undefined) {
			existItem.refCount -= 1;
			if (existItem.refCount <= 0) {
				setContextFileSelected(prev => prev.filter(c => (c as any).refName !== id));
				setCancelContextFile(existItem);
				setChatContext(prev => prev.filter(c => c !== existItem));
			}
		}
	}, []);

	const chooseContextFile = useCallback(
		(file: any, type: string) => {
			const existItem = chatContext.find(item => item.type === type && (item as any).filePath === file.filePath);
			if (!existItem || type === "git") {
				if (mode !== "Agent") {
					let totalSize = 0;
					chatContext.forEach(context => {
						if (context.type === "selection") totalSize += (context as any).selectedText.length;
						else if (context.type === "file") totalSize += (context as any).fileSize;
						else if (context.type === "diagnostic") totalSize += (context as any).contextContent.length;
					});
					totalSize += file.fileSize;
					if (totalSize > CHAT_CONTEXT_LENGTH_LIMIT) {
						Popup.warning(t("chat.codeSelectionLimit") + CHAT_CONTEXT_LENGTH_LIMIT);
						return;
					}
				}
				if (conversation) {
					const currentIndex = conversation.chatContextIndex;

					let withRefNameFile = { ...file, refName: `#ideassistant_ref_${currentIndex}#`, refCount: 1, type };
					if (type === "git") {
						// file: "commit", "branch"
						withRefNameFile = { refName: `#ideassistant_ref_${currentIndex}#`, refCount: 1, type, contextContent: file };
					}
					setChatContext(prev => [...prev, withRefNameFile]);
					MsgCenterWeb.send("updateChatContextIndex", { id: conversation.id, index: currentIndex + 1 });
					setSelectedCodeSegment(withRefNameFile);
					setContextFileSelected(prev => [...prev, withRefNameFile]);
				}
			} else {
				(existItem as any).refCount = ((existItem as any).refCount ?? 0) + 1;
				setSelectedCodeSegment({ ...(existItem as any) });
			}
			setSubCommandRouter("");
		},
		[CHAT_CONTEXT_LENGTH_LIMIT, chatContext, conversation, mode]
	);

	// region Message bridge listeners
	useEffect(() => {
		const handleClickOutside = (event: MouseEvent) => {
			if (isAddCommonExpressionOpen) {
				setIsAddCommonExpressionOpen(false);
			}
			if (!event.target) {
				return;
			}
			//tool-box
			if (toolBoxButtonRef.current && toolBoxButtonRef.current.contains(event.target as Node)) {
				return;
			}

			if (popupRef.current && !popupRef.current.contains(event.target as Node)) {
				setShowPopup("nothing");
			}
		};
		document.addEventListener("mousedown", handleClickOutside);
		return () => document.removeEventListener("mousedown", handleClickOutside);
	}, [showPopup]);

	useEffect(() => {
		MsgCenterWeb.listen("sendDefaultFiles", param => setRecentContextFiles(param));
		MsgCenterWeb.listen("sendProjectFiles", param => setContextFiles(param));
		MsgCenterWeb.listen("sendContextSelection", param => addContextSelection(param));
		MsgCenterWeb.listen("jbUpdateIndex", param => jaUpdataIndex(param));
		MsgCenterWeb.listen("updateWorkAreaList", param => {
			//updateWorkAreaList
			if (convRef.current?.workAreaList) {
				convRef.current.workAreaList = param;
				const newConversation = { ...convRef.current, workAreaList: param };
				setConversation(newConversation as Conversation);
			}
		});
		MsgCenterWeb.listen("updateWorkAreaListDA", param => {
			if (convRef.current?.workAreaList) {
				convRef.current.workAreaList.forEach((item: any) => {
					if (item.path === param.path) {
						item.additions = param.additions;
						item.deletions = param.deletions;
					}
				});
				const newConversation = { ...convRef.current, workAreaList: convRef.current.workAreaList };
				setConversation(newConversation as Conversation);
			}
		});
		MsgCenterWeb.listen("getMainBranch", param => {
			if (param !== "") {
				updateOneCommandText("@branch", `Diff With ${param} Branch`);
			} else {
				updateOneCommandText("@branch", "Main Branch Unknown");
			}
		});
		MsgCenterWeb.listen("addFileToChat", param => addFileToChat(param));

		return () => {
			MsgCenterWeb.unlisten("sendDefaultFiles");
			MsgCenterWeb.unlisten("sendProjectFiles");
			MsgCenterWeb.unlisten("sendContextSelection");
			MsgCenterWeb.unlisten("jbUpdateIndex");
			MsgCenterWeb.unlisten("getMainBranch");
			MsgCenterWeb.unlisten("addFileToChat");
			MsgCenterWeb.unlisten("updateWorkAreaList");
			MsgCenterWeb.unlisten("updateWorkAreaListDA");
		};
	}, []);
	const addFileToChat = useCallback(
		(file: any) => {
			const existItem = chatContextRef.current.find(
				(item): item is FileContext =>
					item.type === "file" &&
					item?.filePath === file.filePath &&
					item?.fileName === file.fileName &&
					item?.fileSize === file.fileSize &&
					item?.fileUriString === file.fileUriString
			);
			if (existItem && existItem.refCount !== undefined) {
				existItem.refCount += 1;
				setSelectedCodeSegment({ ...existItem });
				return;
			}
			let withRefNameFile: any = file;
			if (typeof file === "object" && file) {
				file = JSON.stringify(file);
				withRefNameFile = { ...JSON.parse(file), refCount: 1, isActive: true };
			} else {
				withRefNameFile = { ...file, refCount: 1, isActive: true };
			}
			setChatContext(prev => [...prev, withRefNameFile]);
			setSelectedCodeSegment(withRefNameFile);
			setContextFileSelected(prev => [...prev, withRefNameFile]);
		},
		[mode]
	);

	useEffect(() => {
		isInputValueJustRestoredRef.current = isInputValueJustRestored;
	}, [isInputValueJustRestored]);

	useEffect(() => {
		if (pathname === "/" || conversation?.id !== (panelState as any).selectedConversationId) {
			if (isInputValueJustRestoredRef.current) {
				return;
			}
			setModes([
				{ name: "Ask", value: "Chat" },
				{ name: "Agent", value: "Agent" },
			]);
			reset();
			(async () => {
				const chatList = await fetchAndBuildModels("Chat");
				setModels(chatList);
				setSelectedModel(chatList.find(item => item.isSelected == true)!);
				const agentList = await fetchAndBuildModels("Agent");
				setAgentModels(agentList);
				setSelectedAgentModel(agentList.find(item => item.isSelected == true));
			})();
		}
	}, [pathname, conversation?.id]);

	// region Commands palette
	const handleShowCommand = () => {
		if (!showCommand) MsgCenterWeb.send("getAllFiles");
		if (!showCommand) commandHandle(inputValue);
		setInputFocus(false);
		setShowCommand(!showCommand);
	};

	const commandHandle = useCallback(
		// eslint-disable-next-line @typescript-eslint/no-unused-vars
		async (_command: string) => {
			if (APP_VERSION === "enterprise" && knowledgeBaseNameRef.current.length > 0) {
				const list: IBaseVariableUI[] = knowledgeBaseNameRef.current.map((name: string) => ({
					variable: `@workspace(${name.replace(/^KnowledgeBase__/, "")})`,
					isActive: true,
					text: t("knowledgeBase.ask"),
					disabled: false,
					label: `@Workspace(${name.replace(/^KnowledgeBase__/, "")})`,
					isHideByInput: false,
				}));

				addCachedCommands(list);
				return;
			}
		},
		[APP_VERSION, inputValue, showCommand, knowledgeBaseNameRef.current]
	);

	const fetchKnowledgeBaseName = useCallback(async () => {
		if (APP_VERSION === "enterprise") {
			try {
				const url = `/codeapi/rag/knowledgeBaseInfo?FT_Token=${userId}`;
				const response = await requestHttp.get(url);
				if (response) {
					const data = response;
					const names: any[] = [];
					data.forEach((item: { dirName: any }) => names.push(item.dirName));
					setKnowledgeBaseName(names);
				}
			} catch (e) {
				console.log("getKnowledgeBaseName fetch error", e);
			}
		}
	}, [APP_VERSION]);

	const workspaceFunc = useCallback(
		(val: string) => {
			if (isVSCode || ideType === "jb") commandHandle(val);
		},
		[commandHandle, ideType, isVSCode, inputValue]
	);

	useEffect(() => {
		if (showCommand && conversation?.content.type === "instructionRefinement") {
			setShowCommand(false);
		}
	}, [conversation?.content.type]);

	useEffect(() => {
		const lastAtIndex = editorPrevChar.lastIndexOf("@");
		if (lastAtIndex === -1) {
			setShowCommand(false);
			return;
		}

		// @aaa
		const inputSegment = editorPrevChar.slice(lastAtIndex);

		if (subCommandRouter === "@file") {
			setUserInputSearchFile(inputSegment.replace("@", ""));
			return;
		}

		const matchSourceStringList = getBaseVariables()
			.filter(item => getCommandParentVariable(item.variable) === subCommandRouter)
			.map(item => item.variable);
		const searchResults = fuzzysort.go(inputSegment.toLowerCase(), matchSourceStringList, {
			limit: matchSourceStringList.length,
			threshold: 0.3,
		});
		const matchedCommand = searchResults.map(result => result.target);

		if (matchedCommand.length === 0) {
			setShowCommand(false);
			return;
		}

		updateCachedCommandsHideInput(matchedCommand);
		setTimeout(() => workspaceFunc(inputValue), 0);
		setShowCommand(true);
	}, [editorPrevChar]);

	useEffect(() => {
		const newList = getBaseVariables()
			.filter(item => getCommandParentVariable(item.variable) === subCommandRouter)
			.map(item => item.variable);
		updateCachedCommandsHideInput(newList);
	}, [subCommandRouter]);

	useEffect(() => {
		convRef.current = conversation;
		if (conversation?.id !== (panelState as any).selectedConversationId) return;
		let chatInputType = "";
		let newModes = [
			{ name: "Ask", value: "Chat" },
			{ name: "Agent", value: "Agent" },
		];
		if (
			conversation &&
			Array.isArray((conversation as any).agentResponseList) &&
			(conversation as any).agentResponseList.length > 0
		) {
			newModes = [{ name: "Agent", value: "Agent" }];
		} else if (conversation && "messages" in (conversation as any).content && (conversation as any).content.messages.length > 0) {
			newModes = [{ name: "Ask", value: "Chat" }];
		} else if (
			conversation &&
			"type" in (conversation as any).content &&
			(conversation as any).content.type === "instructionRefinement"
		) {
			newModes = [{ name: "Ask", value: "Chat" }];
		}
		if (conversation && (conversation as any).content.state.type) {
			setChatInputType((conversation as any).content.state.type);
			chatInputType = (conversation as any).content.state.type;
		}
		if (conversation && (conversation as any).mode) {
			let m = (conversation as any).mode;
			if (m === "chat") m = "Chat";
			newModes?.forEach((item, index) => {
				if (m === item.value) setActiveIndex(index);
			});
		}
		if (newModes) setModes(newModes);

		if (chatInputType === "userCanReply" || chatInputType === "userCanRefineInstruction" || isHistory) {
			setIsStreaming(false);
		}
		if (chatInputType === "botAnswerStreaming" || chatInputType === "waitingForBotAnswer") {
			setIsStreaming(true);
			if ((conversation as any).content.type === "instructionRefinement" && (conversation as any).content.error) {
				setIsStreaming(false);
			}
		}
	}, [conversation, conversation?.content, conversation?.content.state]);

	useEffect(() => {
		setImageLabelList([]);
		setSelectImage([]);
		if (fileInputRef.current) fileInputRef.current.value = "";
		setIsAgentMode(mode === "Agent");
		addAdditionalVariables();
	}, [mode, ideType]);

	useEffect(() => {
		chatContextRef.current = chatContext;
	}, [chatContext]);

	useEffect(() => {
		if (chatInputRef.current) setChatInputHeight(chatInputRef.current.clientHeight + 15);

		if (showCommand) {
			const activeVars = cachedCommands.filter(item => item.isActive && !item.isHideByInput);
			const firstEnabledIndexInActive = activeVars.findIndex(item => item.disabled !== true);
			setSelectedIndex(firstEnabledIndexInActive);
		}
	}, [inputValue, showCommand, cachedCommands, mode]);

	useEffect(() => {
		if (conversation && ((conversation as any).mode === "Chat" || (conversation as any).mode === "chat")) {
			fetchKnowledgeBaseName();
		}
		setCommonExpression(ExpressionManager.getAll());
		setChooseCommandsVal("");
	}, [conversation?.mode]);

	useEffect(() => {
		const matchedExpList = ExpressionManager.matchExpression(inputValue, commonExpression);
		if (matchedExpList.length > 0) {
			setShowPopup("common-expression-hint");
			setMatchedCommonExpression(matchedExpList);
		} else {
			if (showPopup === "common-expression-hint") {
				setShowPopup("nothing");
			}
			setMatchedCommonExpression([]);
		}
	}, [inputValue]);

	// region Privacy modal actions
	const handleOk = useCallback(() => {
		localStorage.setItem("hasShownPrivacypolicy", "true");
		setHasShownPrivacy(true);
		setIsModalOpen(false);
	}, []);

	const handleCancel = useCallback(() => {
		setIsModalOpen(false);
		if (mode == "Agent") {
			const updated = models.map((m, i) => ({ ...m, isSelected: i === 0 }));
			setAgentModels(updated);
			setSelectedModel(updated[0]);
			setChatConfigs((prev: any) => ({ ...prev, model: prev.model as AgentModelValue }));
		} else {
			const updated = models.map((m, i) => ({ ...m, isSelected: i === 0 }));
			setModels(updated);
			setSelectedAgentModel(updated[0]);
			setChatConfigs((prev: any) => ({ ...prev, model: updated[0].value as ModelValue }));
		}
	}, [models, agentModels]);

	const sendMessage = useCallback(() => {
		let submitValue = inputValue?.trim() ?? "";
		const hasFailedImage = imageLabelList.some(item => item.status !== "success");
		if (hasFailedImage) {
			Popup.error(t("upload.abnormalImages"));
			return;
		}
		if (mode === "Agent" && !submitValue && imageLabelList.length === 0) {
			setPauseStatus(true);
			MsgCenterWeb.send("startAgent", { id: conversation?.id });
			return;
		}
		const hasImage = Boolean(imageLabelList.length > 0);
		if (!submitValue && !hasImage) return;

		MsgCenterWeb.send("operationCollectionAddUserInput", { msg: submitValue });

		if (mode === "Agent") {
			let chatContextNew = updateChatContext(submitValue, chatContextRef.current as any);
			chatContextNew = appendImageListToChatContext(chatContextNew, imageLabelList);
			[submitValue, chatContextNew] = formatChatContextAndSubmitValue(submitValue, chatContextNew);
			const agentChatConfigs = getAgentChatConfigsData();
			if (agentChatConfigs.agentModel && window._developer) {
				localStorage.setItem("persistentAgentModel", agentChatConfigs.agentModel);
				setPersistentAgentModel(agentChatConfigs.agentModel);
			}
			MsgCenterWeb.send("sendMessage", {
				id: conversation?.id,
				message: submitValue,
				mode,
				chatContext: chatContextNew,
				chatConfigs: agentChatConfigs,
			});
		} else {
			if (hasImage && imageLabelList) {
				// const connectedImageString = imageLabelList.map(item => `![Base64 Image](${item.url})`).join("\n");
				const connectedImageString = `![Base64 Image](${imageLabelList[0].url})`;
				submitValue = submitValue.replace(/#ideassistant_ref_\d{13}#/g, "");
				submitValue = `${connectedImageString}\n${submitValue}`;
			}
			const chatContextNew = updateChatContext(submitValue, chatContextRef.current as any);
			const chatConfigs = getChatConfigsData();
			if (chatConfigs.model && window._developer) {
				localStorage.setItem("persistentModel", chatConfigs.model);
				setPersistentModel(chatConfigs.model);
			}
			MsgCenterWeb.send("sendMessage", {
				id: conversation?.id,
				message: submitValue,
				mode,
				chatContext: chatContextNew,
				chatConfigs: chatConfigs,
			});
		}

		reset();
		globalTimerService.forceSetToZero();
	}, [inputValue, imageLabelList, mode, conversation?.id, chatContext, selectedModel, chatConfigs]);

	const reset = () => {
		setEditorClear(true);
		setChatConfigs({});
		addAdditionalVariables();
		setChooseCommandsVal("");
		setInputValue("");
		setActiveIndex(0);
		setIsStreaming(false);
		setChatContext([]);
		setContextFileSelected([]);

		if (setSelectImage?.length) {
			setSelectImage([]);
		}
		if (fileInputRef.current) fileInputRef.current.value = "";
	};

	const renderButtonContent = () => {
		if (mode === "Agent") {
			return agentButtonContent();
		}
		return normalModeButtonContent();
	};

	const normalModeButtonContent = () => {
		if (isStreaming) {
			return isHover ? (
				<Tooltip title={t("bottomInputArea.stopStreaming")} placement="topRight">
					<StopIcon
						onClick={() => {
							chatInputType === "botAnswerStreaming"
								? MsgCenterWeb.send("chatstreamStatus", { status: false, id: conversation?.id })
								: MsgCenterWeb.send("stopWaitingForBot", { id: conversation?.id });
						}}
					/>
				</Tooltip>
			) : (
				<Tooltip title={t("bottomInputArea.stopStreaming")} placement="topRight">
					<ChatInputLoadingIcon />
				</Tooltip>
			);
		}
		return isHover ? (
			<div onClick={sendMessage}>
				<SendActive />
			</div>
		) : (
			<div>
				<SendSvg />
			</div>
		);
	};

	const agentButtonContent = () => {
		return isStreaming ? (
			isHover ? (
				<Tooltip title={t("bottomInputArea.stopStreaming")} placement="topRight">
					<StopIcon
						onClick={() => {
							MsgCenterWeb.send("stopAgent", { id: conversation?.id });
						}}
					/>
				</Tooltip>
			) : (
				<Tooltip title={t("bottomInputArea.stopStreaming")} placement="topRight">
					<ChatInputLoadingIcon />
				</Tooltip>
			)
		) : isHover ? (
			<div onClick={sendMessage}>
				<SendActive />
			</div>
		) : (
			<div>
				<SendSvg />
			</div>
		);
	};

	// region JSX

	return (
		<div
			className={
				"bottom-input-area relative" +
				((convRef.current?.workAreaList?.length as number) > 0 && mode === "Agent" ? " bottom-input-area_padding" : "")
			}
		>
			{(convRef.current?.workAreaList?.length as number) > 0 && mode === "Agent" && (
				<WorkArea list={convRef.current?.workAreaList} conversationId={convRef.current?.id as string} isHistory={isHistory} />
			)}
			<Modal
				title={privacyModalTitle}
				closable={false}
				keyboard={false}
				maskClosable={false}
				open={isModalOpen}
				onOk={handleOk}
				onCancel={handleCancel}
				okText={privacyModalAgreeText}
				cancelText={privacyModalDisagreeText}
			>
				<p>{privacyModalContent}</p>
			</Modal>

			<div className="bottom-input-area-overflow-gradient-block" />

			<div className="bottom-input-section flex flex-col justify-center items-center relative" ref={chatInputRef}>
				{(conversation as any)?.content.type !== "instructionRefinement" && (
					<div className="tool-area flex justify-between w-full relative">
						<div className="flex justify-start gap-[12px]">
							<button
								className="upload-btn switch-ai-model add-command cursor-pointer flex justify-center items-center transition-all"
								ref={buttonRef}
								onClick={handleShowCommand}
							>
								@<span className="showInputTopBtns">{t("bottomInputArea.addCommand")}</span>
							</button>
							{isP.current !== "Linux" && (
								<label className="upload-btn switch-ai-model add-command cursor-pointer flex justify-center items-center transition-all">
									<Picture />
									<span className="showInputTopBtns">{t("bottomInputArea.addImg")}</span>
									<input
										type="file"
										ref={fileInputRef}
										accept={ImageUploadEventProvider.imageAllowedTypes}
										onChange={handleImageUploadChange}
										multiple={mode !== "Chat"}
									/>
								</label>
							)}
						</div>
						<div className="tool-box" ref={toolBoxButtonRef} onClick={openToolList}>
							<div className="tool-box-icon">
								<SvgPack />
							</div>
						</div>
					</div>
				)}

				{showPopup === "picture" && currentImageUrl && (
					<div className="picture-enlarge-popup absolute z-2000 left-0" ref={popupRef}>
						<PicturePreview text={currentImageText ?? ""} Url={currentImageUrl} onClose={handleClosePopup} />
					</div>
				)}
				{showCommand && (
					<CommandsList
						key="commandList"
						contextFileSelected={contextFileSelected}
						chooseContextFile={chooseContextFile}
						cancelContextFile={cancelContextFile}
						contextFiles={contextFiles}
						recentContextFiles={recentContextFiles}
						knowledgeBaseName={knowledgeBaseName}
						setKnowledgeBaseName={setKnowledgeBaseName}
						// setChooseFileList={setChooseFileList}

						selectedIndex={selectedIndex}
						setSelectedIndex={setSelectedIndex}
						buttonRef={buttonRef.current as HTMLButtonElement}
					/>
				)}

				{showPopup === "common-expression" && <CommonExpressionMainPanel commonExpressionRef={popupRef} />}
				{showPopup === "common-expression-hint" && <CommonExpressionFastPanel commonExpressionFastPanelRef={popupRef} />}
				{showPopup === "rules" && <RulesSettingsPanel isOpen={isRulesSettingsPanelOpen} rulesRef={popupRef} />}

				{showPopup === "tool" && (
					<div className="ideassistant-tools absolute z-10" ref={popupRef}>
						<div className="ideassistant-tools-box">
							{mode === "Chat" && (
								<>
									<div className="ideassistant-tools-box-item" onClick={handleCommonExpressionPopup}>
										<Message />
										<div className="ideassistant-tools-box-2-text">{t("bottomInputArea.addCommonExpression")}</div>
									</div>
									<div className="ideassistant-tools-box-item" onClick={handleRulesPopup}>
										<Ruler />
										<div className="ideassistant-tools-box-2-text">rules</div>
									</div>
								</>
							)}
							{mode === "Agent" && (
								<>
									<div
										className="ideassistant-tools-box-item"
										onClick={() => {
											MsgCenterWeb.send("getMcpInfo");
											setShowMcp(true);
										}}
									>
										<Mcp />
										<div className="ideassistant-tools-box-2-text">MCP</div>
									</div>
									<div className="ideassistant-tools-box-item" onClick={handleRulesPopup}>
										<Ruler />
										<div className="ideassistant-tools-box-2-text">rules</div>
									</div>
								</>
							)}
						</div>
					</div>
				)}

				<McpView open={showMcp} onClose={() => setShowMcp(false)} />

				<div className="input-area w-full">
					<ChatInputEditor
						disabled={
							(conversation as any)?.content.state.type === "waitingForBotAnswer" ||
							(conversation as any)?.content.state.type === "botAnswerStreaming"
						}
						uploadImage={uploadImage}
						selectedCodeSegment={selectedCodeSegment}
						removeFileTag={removeFileTag}
						editorClear={editorClear}
						setEditorClear={setEditorClear}
						chatContext={chatContext}
						setChatContext={setChatContext}
						chatConfigs={chatConfigs}
						setChatConfigs={setChatConfigs}
						// setRealReference={setRealReference}
						// setRealReferenceHeader={setRealReferenceHeader}
						selectedImage={selectImage!}
						setContextFileSelected={setContextFileSelected}
						onSubmit={sendMessage}
						setSelectImage={setSelectImage}
					/>
				</div>

				<div className="bottom-area flex justify-between items-end w-full">
					<div className="models flex justify-start">
						{modes.map((m, index) => (
							<button
								key={index}
								onClick={() => {
									setEditorClear(true);
									MsgCenterWeb.send("setChatMode", { mode: m.value, id: conversation?.id });
									setActiveIndex(index);
								}}
								className={cn(index === activeIndex && "active", "cursor-pointer flex justify-center items-center")}
							>
								{m.name}
							</button>
						))}
						{(conversation as any)?.content.type !== "instructionRefinement" && mode === "Chat" && CAN_CHOOSE_MODEL && (
							<Select
								className="switch-ai-model showSelectModelBtn"
								popupClassName="switch-ai-model-dropdown"
								popupMatchSelectWidth={false}
								value={selectedModel?.value as ModelValue}
								onChange={(value: ModelValue) => modelsChange(value)}
								options={models}
								fieldNames={{ label: "name", value: "value" }}
								optionRender={option => (
									<Space>
										{option.data.name}
										{option.data.isSelected && <SelectedSvg />}
									</Space>
								)}
							/>
						)}
						{mode === "Agent" && window._developer && (
							<Select
								className="switch-ai-model"
								popupClassName="switch-ai-model-dropdown"
								popupMatchSelectWidth={false}
								value={selectedAgentModel?.value as AgentModelValue}
								onChange={(value: AgentModelValue) => agentModelsChange(value)}
								options={agentModels}
								fieldNames={{ label: "name", value: "value" }}
								optionRender={option => (
									<Space>
										{option.data.name}
										{option.data.isSelected && <SelectedSvg />}
									</Space>
								)}
							/>
						)}
					</div>

					<div className="send-message" onMouseEnter={() => setIsHover(true)} onMouseLeave={() => setIsHover(false)}>
						{renderButtonContent()}
					</div>
				</div>
			</div>
		</div>
	);
};
