import { requestHttp } from "@/api";
import type { DefaultOptons, Options } from "./types";

enum EventType {
	"send-icon" = "sendMessageEvent",
}

const send_time: number = 1000 * 60 * 10;

function getCurrentDate() {
	const date = new Date();
	const dateString = date.toISOString().split("T")[0];
	return dateString;
}

export default class Tracker {
	private static instance: Tracker;
	private static timer: NodeJS.Timeout | null = null;
	private data: Options;
	// private eventList: string[] = ['click', 'keydown', 'keyup', 'keypress', 'focus', 'blur',]
	private eventList: string[] = ["click"];
	private classList: string[] = ["send-icon", "stop-icon2"];

	private constructor(options: Options) {
		this.data = Object.assign(this.initConfig(), options);
		this.installExtra();
	}

	public static getInstance(options: Options) {
		if (!Tracker.instance) {
			Tracker.instance = new Tracker(options);
			Tracker.setTimer();
		}
		return Tracker.instance;
	}

	private static setTimer() {
		if (Tracker.timer === null) {
			Tracker.timer = setInterval(() => {
				if (!window.localStorage.getItem("tacker_queue")) return;
				Tracker.instance.fetchReport(Tracker.instance.data.requestUrl, window.localStorage.getItem("tacker_queue"));
			}, send_time);
		}
	}

	private domTracker() {
		this.eventList.forEach(item => {
			window.addEventListener(item, e => {
				const element = e.target as HTMLElement;
				if (this.classList.includes(element.className)) {
					const type = EventType[element.className as keyof typeof EventType];
					this.sendData({ tracker_type: item, tracker_event_type: type });
				}
			});
		});
	}

	public addTrackerData<T>(data: T) {
		this.sendData(data);
	}

	private sendData<T>(data: T) {
		const params = Object.assign({}, data, { user_id: this.data.user_id || this.data.extra?.user_id, tracker_time: Date.now() });
		let queue = window.localStorage.getItem("tacker_queue")
			? JSON.parse(window.localStorage.getItem("tacker_queue") as string)
			: [];
		if (queue) queue.push(params);
		else queue = [params as never];
		window.localStorage.setItem("tacker_queue", JSON.stringify(queue));
	}

	async fetchReport<T>(url: string, params: T) {
		try {
			const data_str = "tracker=" + params;
			const requestUrl = url + data_str;
			const response = await requestHttp.get(requestUrl);
			window.localStorage.removeItem("tacker_queue");
		} catch (error) {
			console.error("There has been a problem with your fetch operation:", error);
		}
	}

	private jsError() {
		window.onerror = (message, source, lineno, colno, error) => {
			if (error) {
				this.sendData({
					tracker_type: "jsError",
					tracker_err_msg: message,
					tracker_error: error,
					tracker_source: source,
					tracker_lineno: lineno,
					tracker_colno: colno,
				});
			}
		};
		window.addEventListener(
			"error",
			e => {
				e.preventDefault();
				const isErrorEvent: boolean = e instanceof ErrorEvent;
				if (!isErrorEvent) {
					this.sendData({ type: "resourceError", msg: e.message });
					return;
				}
				this.sendData({ tracker_type: "jsError", tracker_err_msg: e.message });
			},
			true
		);
		window.addEventListener("unhandledrejection", (e: PromiseRejectionEvent) => {
			e.preventDefault();
			e.promise.catch(error => {
				const msg = error?.message || error;
				this.sendData({ tracker_type: "promiseError", tracker_err_msg: msg });
			});
		});
	}

	public installExtra() {
		if (this.data.domTracker) {
			this.domTracker();
		} else if (this.data.jsError) {
			this.jsError();
		} else if (this.data.extra) {
			this.sendData(this.data.extra);
		}
	}

	private initConfig(): DefaultOptons {
		return <DefaultOptons>{
			domTracker: false,
			jsError: false,
		};
	}
}
