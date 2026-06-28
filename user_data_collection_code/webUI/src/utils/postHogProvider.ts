import posthog, { type PostHog } from "posthog-js";
import { globalConfig } from "@/globalConfig";

export class PHSingleton {
	private static _instance: PHSingleton | null = null;
	private client: PostHog | null = null;
	private initialized = false;
	private postHogTagDisabled = true;
	// eslint-disable-next-line @typescript-eslint/no-empty-function
	private constructor() {}

	static get instance() {
		if (!PHSingleton._instance) {
			PHSingleton._instance = new PHSingleton();
		}
		return PHSingleton._instance;
	}

	init(opts?: { apiKey?: string; host?: string }) {
		if (this.initialized) return;

		if (globalConfig.APP_VERSION === "default" || globalConfig.APP_VERSION === "saas") {
			this.postHogTagDisabled = false;
		}

		const apiKey = opts?.apiKey ?? import.meta.env.VITE_REACT_APP_POSTHOG_API_KEY;
		const host = opts?.host ?? import.meta.env.VITE_REACT_APP_POSTHOG_HOME_URL;

		if (!apiKey) {
			console.warn("[PH] PostHog disabled (missing API Key).");
			return;
		}

		this.client = posthog.init(apiKey, {
			api_host: host,
			opt_out_capturing_by_default: this.postHogTagDisabled,
		});

		this.initialized = true;
	}

	get raw(): PostHog | null {
		return this.client;
	}

	getFeatureFlag(key: string, defaultValue: boolean) {
		if (!this.client) return defaultValue;
		return this.client.getFeatureFlag(key);
	}

	capture(event: string, props?: Record<string, any>) {
		if (!this.client) return;
		this.client.capture(event, props);
	}

	identify(id: string, props?: Record<string, any>) {
		if (!this.client) return;
		this.client.identify(id, props);
	}

	setUserProperties(props: Record<string, any>) {
		if (!this.client) return;
		this.client.people?.set(props);
	}
	register(props: Record<string, any>) {
		if (!this.client) return;
		this.client.register(props);
	}

	reset() {
		if (!this.client) return;
		this.client.reset();
	}
}

export const PH = PHSingleton.instance;
