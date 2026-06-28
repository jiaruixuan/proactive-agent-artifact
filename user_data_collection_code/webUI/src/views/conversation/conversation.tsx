import "@/styles/pages/conversation/conversation.scss";
import { lazy, memo, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import AutoSizer from "react-virtualized-auto-sizer";
import { VariableSizeList as List } from "react-window";
import { requestHttp } from "@/api";
import { Popup } from "@/components/utils/popup/popup";
import { t } from "@/language";
import { MsgCenterWeb } from "@/messageCenter/msgCenterWeb";
import { useRouteParams } from "@/router/routeUtils";
import { useGlobalStore } from "@/store";
import type { ChatContextListToPlugin, Context } from "@/store/interface";
import Tracker from "@/tracker/index";

const ConversationItem = lazy(() => import("./conversationItem"));

const DEFAULT_ITEM_HEIGHT = 120;
const BOTTOM_THRESHOLD = 50;

export const ConversationPage: React.FC = memo(() => {
	const params = useRouteParams();
	const navigate = useNavigate();
	const conversationId = params.conversationId;
	const { conversation, panelState, setConversation, globalConfig, serverUrl, userId } = useGlobalStore();

	const renderedMessagesRef = useRef<any[]>([]);
	const [conversationList, setConversationList] = useState<any[]>([]);
	const [chatConfigs, setChatConfigs] = useState<any>({});
	const [stateType, setStateType] = useState<string>(conversation?.content?.state?.type || "");
	const [isRegenerating, setIsRegenerating] = useState(false);
	const [isThumbsupfilled, setIsThumbsupfilled] = useState(false);
	const [isThumbsdownfilled, setIsThumbsdownfilled] = useState(false);
	const [loading, setLoading] = useState(false);
	const containerRef = useRef<HTMLDivElement>(null);
	const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
	const [userInterrupted, setUserInterrupted] = useState(false);
	const scrollTimeout = useRef<NodeJS.Timeout>();
	const isScrollingRef = useRef(false);
	const hasDisplayedErrorRef = useRef(false);
	const sizeMapRef = useRef<Record<number, number>>({});
	const listRef = useRef<any>(null);
	const rowHeights = useRef<{ [key: number]: number }>({});
	const saveTimeoutRef = useRef<any>(null);
	const sizeCacheKey = useCallback(() => {
		return `conversation_row_heights_${conversationId || "global"}`;
	}, [conversationId]);

	const loadSizeCache = useCallback(() => {
		try {
			if (!conversationId) return;
			const raw = localStorage.getItem(sizeCacheKey());
			if (!raw) return;
			const parsed = JSON.parse(raw) as Record<number, number>;
			sizeMapRef.current = { ...(sizeMapRef.current || {}), ...parsed };
			rowHeights.current = { ...(rowHeights.current || {}), ...parsed };
		} catch (e) {
			console.log("loadSizeCache err", e);
		}
	}, [sizeCacheKey, conversationId]);

	const saveSizeCache = useCallback(() => {
		try {
			if (!conversationId) return;
			localStorage.setItem(sizeCacheKey(), JSON.stringify(sizeMapRef.current || {}));
		} catch (e) {
			console.log("saveSizeCache err", e);
		}
	}, [sizeCacheKey, conversationId]);

	/* ---------- Tracker ---------- */
	const setTracker = useCallback(
		(type: string) => {
			const options = {
				requestUrl: "/codeuser/statistic_log?",
				user_id: userId,
				extra: {
					tracker_type: "click",
					tracker_event_type: type,
					tracker_event_options: conversationList.filter(item => item.tracedata),
				},
			};
			Tracker.getInstance(options).addTrackerData(options.extra);
		},
		[userId, conversationList]
	);

	const gotoContext = useCallback((params: any) => {
		MsgCenterWeb.send("gotoContext", {
			fileUriString: params.fileUriString,
			range: params.range || undefined,
			type: params.type,
		});
	}, []);

	const generateMessageId = useCallback((message: any, index: number) => {
		if (message.id) return message.id;
		const content = typeof message.content === "string" ? message.content : JSON.stringify(message.content);

		const encodedContent = encodeURIComponent(content?.slice(0, 100));
		const hash = encodedContent.replace(/[^a-zA-Z0-9]/g, "").slice(0, 10);
		return `${message?.author || "unknown"}_${index}_${hash}`;
	}, []);

	const updateConversationList = useCallback((newList: any[]) => {
		setIsThumbsupfilled(false);
		setIsThumbsdownfilled(false);
		setConversationList(prevList => {
			const currentRendered = renderedMessagesRef.current;
			if (newList.length <= currentRendered.length) {
				renderedMessagesRef.current = newList;
				return newList;
			}
			const newMessages = newList.slice(currentRendered.length);
			const updatedList = [...currentRendered, ...newMessages];
			renderedMessagesRef.current = updatedList;
			setShouldAutoScroll(true);
			return updatedList;
		});
	}, []);

	const scrollToBottom = useCallback(
		(instant = false) => {
			if (!shouldAutoScroll) return;
			if (listRef.current) {
				if (listRef.current && conversationList.length) {
					const lastIdx = conversationList.length - 1;
					listRef.current.scrollToItem(lastIdx, "end");
					setTimeout(() => {
						const outer = listRef.current?.outerRef;
						if (outer) {
							outer.scrollTop += 10;
						}
					}, 0);
				}
			} else if (containerRef.current) {
				const container = containerRef.current;
				const targetScrollTop = container.scrollHeight - container.clientHeight;
				if (instant) {
					container.scrollTop = targetScrollTop;
				} else {
					container.scrollTo({ top: targetScrollTop, behavior: "smooth" });
				}
			}
		},
		[conversationList.length, shouldAutoScroll]
	);

	const debouncedScrollToBottom = useCallback(
		(instant = false) => {
			if (scrollTimeout.current) clearTimeout(scrollTimeout.current);
			scrollTimeout.current = setTimeout(() => scrollToBottom(instant), 50);
		},
		[scrollToBottom]
	);

	const handleWheel = useCallback((e: WheelEvent) => {
		setUserInterrupted(true);
		setShouldAutoScroll(false);
		if (scrollTimeout.current) clearTimeout(scrollTimeout.current);
		scrollTimeout.current = setTimeout(() => {
			const container = containerRef.current;
			if (!container) return;
			const { scrollTop, scrollHeight, clientHeight } = container;
			const isAtBottom = scrollTop + clientHeight >= scrollHeight - 20;
			if (isAtBottom) {
				setUserInterrupted(false);
			}
		}, 1000);
	}, []);

	useEffect(() => {
		const container = containerRef.current;
		if (!container) return;
		container.addEventListener("wheel", handleWheel, { passive: true });
		return () => {
			container.removeEventListener("wheel", handleWheel);
		};
	}, [handleWheel]);
	const setRowHeight = (index: number, size: number) => {
		console.log("setRowHeight", index, size);
		const prev = rowHeights.current[index] || sizeMapRef.current[index] || 0;
		if (Math.abs(prev - size) > 1) {
			rowHeights.current = { ...rowHeights.current, [index]: size };
			sizeMapRef.current = { ...sizeMapRef.current, [index]: size };
			listRef.current?.resetAfterIndex(index, true);
			if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
			saveTimeoutRef.current = setTimeout(() => {
				saveSizeCache();
			}, 300);
		}
	};

	const Row = memo(({ index, style }: { index: number; style: React.CSSProperties }) => {
		const item = conversationList[index];
		const rowRef = useRef<HTMLDivElement>(null);

		useEffect(() => {
			const el = rowRef.current;
			if (!el) return;

			const resizeObserver = new ResizeObserver(entries => {
				for (const entry of entries) {
					const newHeight = entry.contentRect.height;
					setRowHeight(index, newHeight);
				}
			});

			resizeObserver.observe(el);
			return () => {
				resizeObserver.disconnect();
			};
		}, [index, item, setIsToolExpand]);

		return (
			<div style={style}>
				<div ref={rowRef}>
					<ConversationItem
						key={item.id || `vrow_${index}`}
						item={item}
						index={index}
						conversation={conversation}
						conversationList={conversationList}
						stateType={stateType}
						isThumbsupfilled={isThumbsupfilled}
						isThumbsdownfilled={isThumbsdownfilled}
						loading={loading}
						onLikeClick={handleLikeClick}
						onDislikeClick={handleDislikeClick}
						onExportClick={handleExportClick}
						onRegenerateClick={handleRegenerateClick}
						onToolExpand={setIsToolExpand}
						replaceRefs={replaceRefs}
					/>
				</div>
			</div>
		);
	});

	const setIsToolExpand = (isToolExpand: boolean, index: number) => {
		setConversationList(prev => {
			const newList = [...prev];
			newList[index].isToolExpand = isToolExpand;
			return newList;
		});
	};

	const handleLikeClick = () => {
		if (!isThumbsupfilled) {
			setTracker("thumbsup");
			setIsThumbsupfilled(true);
			setIsThumbsdownfilled(false);
		}
	};

	const handleDislikeClick = () => {
		if (!isThumbsdownfilled) {
			setTracker("thumbsdown");
			setIsThumbsdownfilled(true);
			setIsThumbsupfilled(false);
		}
	};

	const handleShareClick = async () => {
		setTracker("share");
		const date = new Date().toLocaleDateString().replace(/\//g, "");
		let ss = JSON.stringify(conversation);
		if (ss.length > 10 * 1024 * 1024) {
			Popup.error(t("chat.share.tooLangToShare"));
			return;
		}
		const newId = Math.random().toString(36).substr(2, 9);
		ss = ss.replace(conversation?.id as string, newId);
		const response = await requestHttp.post("/codeuser/store", { path: `${date}_${newId}`, data: ss });
		if (response) {
			let shareLink = serverUrl.replace("/codeuser", "");
			if (globalConfig.APP_VERSION !== "default") {
				shareLink += "/webchat";
			}
			shareLink += `?share=${date}_${newId}`;
			MsgCenterWeb.send("openLink", shareLink);
		} else {
			Popup.error(t("chat.share.failed"));
		}
	};
	function getPrev(index: number): any {
		for (let i = index; i >= 0; i--) {
			if (conversationList[i].agentType === "user_feedback") {
				return i;
			}
		}
	}

	function splitMessagesByUserFeedback(conversationList: any[], index: number): string {
		if (!Array.isArray(conversationList) || conversationList.length === 0) {
			return "";
		}
		const prev = getPrev(index);
		let blockArray = "";
		for (let i = prev + 1; i <= index; i++) {
			if (conversationList[i] && conversationList[i].agentType !== "thinking") {
				if (conversationList[i].agentType === "tool") {
					const content = JSON.parse(conversationList[i].content);
					const blockContent = `['${content.toolName}' called]\n`;
					blockArray += blockContent;
				} else {
					blockArray += conversationList[i].content;
				}
			}
		}
		return blockArray;
	}
	const handleExportClick = () => {
		setTracker("export");
		if (conversation?.mode === "Agent") {
			MsgCenterWeb.send("exportAgentConversation", { id: conversation?.id });
		} else {
			MsgCenterWeb.send("exportConversation", { id: conversation?.id });
		}
	};

	const handleCopyClick = (conversation: any, index: number) => {
		let copyText = "";
		if (Object.prototype.hasOwnProperty.call(conversation, "agentType")) {
			copyText = splitMessagesByUserFeedback(conversationList as any[], index);
		} else {
			copyText = conversation.content;
		}
		MsgCenterWeb.send("copyText", {
			text: copyText,
		});
		Popup.success(t("copySuccess"));
	};

	const handleRegenerateClick = () => {
		setTracker("restart");
		setIsRegenerating(true);
		setShouldAutoScroll(true);
		MsgCenterWeb.send("regenerate", { id: conversation?.id });
		setTimeout(() => setIsRegenerating(false), 2000);
	};

	const getChatContextList = useCallback(
		(index: number) => {
			const list: any[] = [];
			const mode = conversation?.mode;

			if (mode === "chat") {
				if (index === -1) {
					conversationList.forEach(item => {
						if (item?.author === "user" || item.agentType === "user_feedback") {
							if (item.chatRequest?.chatContext) list.push(...item.chatRequest.chatContext);
							if (item.chatContext) list.push(...item.chatContext);
						}
					});
					return list;
				}
				const item = conversationList[index];
				if (item?.author === "user" || item.agentType === "user_feedback") {
					if (item.chatRequest?.chatContext) list.push(...item.chatRequest.chatContext);
					if (item.chatContext) list.push(...item.chatContext);
				}
				return list;
			} else {
				index += 1;
				if (conversation && conversation.agentResponseList) {
					const agentResponseList = conversation?.agentResponseList;
					index = Math.min(agentResponseList.length - 1, index);

					for (let i = index; i >= 0; i--) {
						const item = agentResponseList[i];
						if (item && item.agentType === "user_feedback") {
							if (item.chatContext) list.push(...item.chatContext);
							break;
						}
					}
				}
				return list;
			}
		},
		[conversationList, conversation]
	);

	const replaceCommandTag = (inputText: string) => {
		const tagRegex = /(?:^|\s)(`?@(?:web|workspace|terminal|report)(?:\(.*?\))?`?)(?![^[]*\])/g;
		return inputText.replace(tagRegex, match => {
			return `<span class="file-reference-block" contenteditable="false">
				<span class="file-node">
					<span class="text-content">${match}</span>
				</span>
			</span>`;
		});
	};

	/**
	 * @param matchString  "#ideassistant_ref_x#"
	 * @param chatContextList [{...}, {...}, {...}]
	 * @returns
	 */
	const getRefObjectByMatchString = (matchString: string, chatContextList: ChatContextListToPlugin): Context => {
		for (const chatContext of chatContextList) {
			if (chatContext.type !== "image") {
				if (chatContext.refName === matchString) {
					return chatContext;
				}
			} else {
				const matches = matchString.match(/(?:`#ideassistant_img_(\d+)#`|#ideassistant_img_(\d+)#)/g);
				if (matches) {
					for (const match of matches) {
						const idMatch = match.match(/#ideassistant_img_(\d+)#/);
						if (idMatch) {
							const id = idMatch[1];
							return { ...chatContext.images[Number(id) - 1], type: "image", id: Number(id) };
						}
					}
				} else {
					for (const imageItem of chatContext.images) {
						if (imageItem.name === matchString) {
							return { ...imageItem, type: "image", id: Number(imageItem.id) };
						}
					}
				}
			}
		}
		return {
			type: "file",
			fileName: "unknown",
			filePath: "",
			refName: "unknown",
			fileSize: 0,
		};
	};

	/**
	 * @returns
	 */
	const replaceRefs = (inputText: string, index: number) => {
		const text = replaceCommandTag(inputText);
		const refsData = getChatContextList(index);
		if (refsData.length === 0) {
			return text;
		}
		const regex = /(?:`#ideassistant_(ref|img)_(\d+)#`|#ideassistant_(ref|img)_(\d+)#)/g;
		return text.replace(regex, match => {
			const cleanMatch = match.replace(/`/g, "");
			const ref = getRefObjectByMatchString(cleanMatch, refsData);
			if (!ref) return match;
			switch (ref.type) {
				case "file":
					return `<span class="file-reference-block" data-type="file" data-uri="${ref.fileUriString}" data-id="${ref.refName}" contenteditable="false">
						<span class="file-node">
							<span class="text-content">${ref.fileName}</span>
						</span>
					</span>`;
				case "selection":
					return `<span class="file-reference-block" data-type="selection" data-uri="${ref.fileUriString}"
						data-range="${ref.selectedRange.start.line}:${ref.selectedRange.start.character}-${ref.selectedRange.end.line}:${ref.selectedRange.end.character}"
						data-id="${ref.refName}" contenteditable="false">
							<span class="file-node">
								<span class="text-content">${ref.fileName}</span>
								<span class="file-selection">${ref.selectedRange.start.line + 1}-${ref.selectedRange.end.line + 1}</span>
							</span>
						</span>`;
				case "diagnostic": {
					const message = ref.message.length > 20 ? ref.message.slice(0, 20) + "..." : ref.message;
					return `<span class="file-reference-block" data-type="diagnostic" data-uri="${ref.fileUriString}"
							data-range="${ref.range.start.line}:${ref.range.start.character}-${ref.range.end.line}:${ref.range.end.character}"
							data-id="${ref.refName}" contenteditable="false">
								<span class="file-node">
									<span class="text-content">${ref.fileName}: ${message}</span>
								</span>
							</span>`;
				}
				case "terminal":
					return `<span class="file-reference-block" data-type="terminal" data-id="${ref.refName}" contenteditable="false">
						<span class="file-node">
							<span class="text-content">${t("Terminal Info")}</span>
						</span>
					</span>`;
				case "image":
					return `<span class="file-reference-block file-reference-block-image" data-type="image" >
						<span class="file-node inline-flex items-center cursor-pointer">
							<span class="hover-image-wrapper inline-flex items-center">
								<img src="${ref.url}" alt="${ref.text}" class="hover-image" />
							</span>
							<span class="text-content">${ref.text}</span>
						</span>
					</span>`;
				case "git":
					return `<span class="file-reference-block" data-type="terminal" data-id="${ref.refName}" contenteditable="false">
						<span class="file-node">
							<span class="text-content">${ref.contextContent}</span>
						</span>
					</span>`;
				default:
					return "";
			}
		});
	};

	useEffect(() => {
		if (conversationId && panelState && "conversations" in panelState && panelState?.conversations) {
			const conv = panelState.conversations.find(c => c.id === conversationId);
			setConversation(conv);
		}
	}, [conversationId, panelState]);

	useEffect(() => {
		if (!conversation) return;
		const { content, agentResponseList, mode } = conversation;
		setStateType(content.state.type);
		setLoading(content.state.type === "botAnswerStreaming" || content.state.type === "waitingForBotAnswer");
		let list: any[] = [];
		if (agentResponseList?.length) {
			list = agentResponseList;
		} else if (content.type === "messageExchange") {
			const messages = [...content.messages];
			messages.filter((msg: any) => {
				if (msg?.author === "user") {
					setChatConfigs(msg.chatRequest?.configs ?? {});
				}
			});
			if (content.state.type === "botAnswerStreaming" && mode !== "Agent") {
				messages.push({
					author: "bot",
					content: content.state.partialAnswer,
					isStreaming: true,
				});
			}
			if (content.error && !hasDisplayedErrorRef.current) {
				hasDisplayedErrorRef.current = true;
				setLoading(false);
				messages.push({
					author: "bot",
					error: content.error,
					content: "",
					id: `bot_error_${Date.now()}`,
				});
			} else {
				hasDisplayedErrorRef.current = false;
			}
			const messagesWithId = messages.map((msg, idx) => {
				if (msg.id) return msg;
				if ((msg as any).isStreaming) {
					const stableStreamId = conversationId ? `${conversationId}_bot_streaming` : `bot_streaming`;
					return { ...msg, id: stableStreamId };
				}
				return {
					...msg,
					id: generateMessageId(msg, idx),
				};
			});
			list = [...list, ...messagesWithId];
		}
		if (list.length === 0) {
			navigate("/");
		}

		updateConversationList(list);
	}, [
		conversation,
		generateMessageId,
		updateConversationList,
		conversation?.content,
		conversation?.content.state,
		conversation?.content.error,
		loadSizeCache,
		navigate,
	]);

	useEffect(() => {
		const handleClick = (event: MouseEvent) => {
			const target = (event.target as HTMLElement).closest(".file-reference-block");
			if (target) {
				gotoContext({
					type: target.getAttribute("data-type"),
					range: target.getAttribute("data-range") || undefined,
					fileUriString: target.getAttribute("data-uri"),
				});
			}
		};
		document.addEventListener("click", handleClick);
		return () => {
			document.removeEventListener("click", handleClick);
		};
	}, [gotoContext]);

	useEffect(() => {
		const previewDiv = document.createElement("div");
		previewDiv.classList.add("conversation-page-image-tag-hover-mouse-area");
		const img = document.createElement("img");
		previewDiv.appendChild(img);
		document.body.appendChild(previewDiv);

		let currentTarget: HTMLElement | null = null;
		let lastMouseEvent: MouseEvent | null = null;

		const positionPreview = (e: MouseEvent) => {
			if (!previewDiv) return;
			const offset = 14;
			const vw = window.innerWidth;
			const vh = window.innerHeight;

			previewDiv.style.display = "flex";
			previewDiv.style.opacity = "0";

			const rect = previewDiv.getBoundingClientRect();
			const rectW = rect.width || 200;
			const rectH = rect.height || 150;

			let left = e.clientX + offset;
			let top = e.clientY + offset;

			if (left + rectW > vw - 8) {
				left = e.clientX - offset - rectW;
			}
			if (top + rectH > vh - 8) {
				top = e.clientY - offset - rectH;
			}
			if (left < 8) left = 8;
			if (top < 8) top = 8;

			previewDiv.style.left = `${left}px`;
			previewDiv.style.top = `${top}px`;
			previewDiv.style.opacity = "1";
		};

		const onMouseOver = (e: MouseEvent) => {
			const target = (e.target as HTMLElement).closest(".hover-image-wrapper, .file-reference-block-image");
			if (!target) return;
			const smallImg = (target as HTMLElement).querySelector("img.hover-image") as HTMLImageElement | null;
			if (!smallImg) return;
			const largeSrc = smallImg.getAttribute("data-large-src") || smallImg.src;
			if (!largeSrc) return;
			currentTarget = target as HTMLElement;
			lastMouseEvent = e;

			img.onload = () => {
				if (lastMouseEvent) positionPreview(lastMouseEvent);
			};
			img.src = largeSrc;
			positionPreview(e);
		};

		const onMouseMove = (e: MouseEvent) => {
			lastMouseEvent = e;
			if (!currentTarget) return;
			positionPreview(e);
		};

		const onMouseOut = (e: MouseEvent) => {
			if (!currentTarget) return;
			const related = e.relatedTarget as HTMLElement | null;
			if (related && currentTarget.contains(related)) {
				return;
			}
			previewDiv.style.display = "none";
			previewDiv.style.opacity = "0";
			img.src = "";
			currentTarget = null;
			lastMouseEvent = null;
		};

		document.addEventListener("mouseover", onMouseOver);
		document.addEventListener("mousemove", onMouseMove);
		document.addEventListener("mouseout", onMouseOut);

		return () => {
			document.removeEventListener("mouseover", onMouseOver);
			document.removeEventListener("mousemove", onMouseMove);
			document.removeEventListener("mouseout", onMouseOut);
			if (previewDiv && previewDiv.parentElement) previewDiv.parentElement.removeChild(previewDiv);
		};
	}, []);

	useEffect(() => {
		if (containerRef.current && shouldAutoScroll) {
			requestAnimationFrame(() => {
				const isStreaming = conversation?.content.state.type === "botAnswerStreaming";
				debouncedScrollToBottom(!isStreaming);
			});
		}
	}, [conversationList, shouldAutoScroll, conversation?.content.state.type]);

	useEffect(() => {
		return () => {
			if (scrollTimeout.current) {
				clearTimeout(scrollTimeout.current);
			}
			if (saveTimeoutRef.current) {
				clearTimeout(saveTimeoutRef.current);
			}
		};
	}, []);

	const renderContent = () => {
		const userLen = conversationList.filter(item => item?.author === "user" || item?.agentType === "user_feedback").length;
		if (conversation?.mode === "Chat") {
			if (userLen <= 20) {
				return conversationList.map((item, index) => (
					<ConversationItem
						key={item.id || `conversation_${index}`}
						item={item}
						index={index}
						conversation={conversation}
						conversationList={conversationList}
						stateType={stateType}
						isThumbsupfilled={isThumbsupfilled}
						isThumbsdownfilled={isThumbsdownfilled}
						loading={loading}
						onLikeClick={handleLikeClick}
						onDislikeClick={handleDislikeClick}
						onCopyClick={() => handleCopyClick(item, index)}
						onExportClick={handleExportClick}
						onRegenerateClick={handleRegenerateClick}
						onToolExpand={setIsToolExpand}
						replaceRefs={replaceRefs}
						chatConfigs={chatConfigs}
					/>
				));
			}
			return (
				<AutoSizer>
					{({ height, width }: { height: number; width: number }) => (
						<List
							ref={listRef}
							outerRef={containerRef}
							outerElementType="div"
							height={height}
							itemCount={conversationList.length}
							itemSize={(i: number) => rowHeights.current[i] || sizeMapRef.current[i] || DEFAULT_ITEM_HEIGHT}
							width={width}
							overscanCount={5}
							className="conversation-item"
						>
							{Row}
						</List>
					)}
				</AutoSizer>
			);
		}
		return conversationList.map((item, index) => (
			<ConversationItem
				key={item.id || `conversation_${index}`}
				item={item}
				index={index}
				conversation={conversation}
				conversationList={conversationList}
				stateType={stateType}
				isThumbsupfilled={isThumbsupfilled}
				isThumbsdownfilled={isThumbsdownfilled}
				loading={loading}
				onLikeClick={handleLikeClick}
				onDislikeClick={handleDislikeClick}
				onCopyClick={() => handleCopyClick(item, index)}
				onExportClick={handleExportClick}
				onRegenerateClick={handleRegenerateClick}
				onToolExpand={setIsToolExpand}
				replaceRefs={replaceRefs}
				chatConfigs={chatConfigs}
			/>
		));
	};

	return (
		<div
			className={`conversation-page h-full ${conversation?.workAreaList?.length > 0 ? "convBottom" : ""}`}
			ref={containerRef}
			style={{ overflowY: "auto" }}
		>
			{renderContent()}
		</div>
	);
});
