import * as Sentry from "@sentry/react";
import axios, { type AxiosError, type AxiosInstance, type AxiosRequestConfig, type AxiosResponse } from "axios";
import { Popup } from "@/components/utils/popup/popup";
import { MsgCenterWeb } from "@/messageCenter/msgCenterWeb";
import { Env } from "@/utils/getEnv";
import { parseUrlParams } from "@/utils/util";
import { globalConfig } from "../globalConfig";


interface TokenResponse {
	access_token?: string;
	refresh_token?: string;
	status_code?: number;
	msg?: string;
}

interface RequestHttpOptions {
	baseURL?: string;
	timeout?: number;
	headers?: Record<string, string>;
}

class AuthError extends Error {
	status: number;
	data: unknown;
	constructor(message: string, status: number, data: unknown) {
		super(message);
		this.status = status;
		this.data = data;
		this.name = "AuthError";
	}
}

interface logoutMsg {
	message: string;
}

const HINDE_POPUP_URLS = ["fb_check_login_auth", "sso_check_login", "statistic_log", "get_notifications", "click_count"];
function popError(url: string | undefined, popupString: string) {
	const shouldShowError = !HINDE_POPUP_URLS.some(disallowedUrl => url?.includes(disallowedUrl));
	if (shouldShowError) {
		Popup.error(popupString || "Request failed");
	}
}

export function forceLogOut(msg?: logoutMsg) {
	localStorage.removeItem("accessToken");
	localStorage.removeItem("refreshToken");
	localStorage.removeItem("userId");
	MsgCenterWeb.send("forceLogOut", msg);
}
class RequestHttp {
	private instance: AxiosInstance;
	private isRefreshing = false;
	private failedQueue: ((token: string) => void)[] = [];
	private baseURL = Env.isDev ? "" : globalConfig.APP_API_URL;

	constructor(options: RequestHttpOptions = {}) {
		const isEnterprise = globalConfig.APP_VERSION === "enterprise";
		if (isEnterprise) {
			const links = document.querySelectorAll("link");
			if (links.length > 0) {
				const baseURI = parseUrlParams(links[0].baseURI);
				this.baseURL = baseURI.serverUrl;
			}
		}
		this.instance = axios.create({
			baseURL: this.baseURL,
			timeout: options.timeout ?? 10000,
			headers: {
				"Content-Type": "application/json",
				"X-API-Version": "v2",
				...options.headers,
			},
		});

		this.setupInterceptors();
	}

	private setupInterceptors() {
		this.instance.interceptors.request.use((config: any) => {
			const token = localStorage.getItem("accessToken");
			if (token) {
				config.headers = { ...config.headers, Authorization: `Bearer ${token}` };
			}
			return config;
		});

		this.instance.interceptors.response.use(
			res => this.handleResponse(res),
			err => this.handleError(err)
		);
	}

	private handleResponse(res: AxiosResponse) {
		const { status, data, config } = res;
		const url = config.url;
		if (status >= 200 && status < 300) {
			if (url?.includes("/codeapi")) {
				return data;
			}
			if (data.code === -1) {
				popError(url, data.msg);
				return null;
			}
			return data.data;
		}
		throw new AuthError(data?.msg || "Request failed", status, data);
	}

	private async handleError(error: AxiosError) {
		const originalRequest = error.config as AxiosRequestConfig & { _retry?: boolean };
		const response: AxiosResponse | undefined = error.response; // status_code detail

		if (!response) {
			if (error.message.includes("timeout")) {
				Popup.error("Request timeout");
			} else {
				Popup.error("Request error");
			}
			return Promise.reject(error);
		}

		const { status, data } = response;
		const errorMsg = data?.detail || data?.message || this.getStatusMessage(status);
		if (status !== 401) {
			popError(originalRequest.url, errorMsg);
			return Promise.reject(new AuthError(errorMsg, status, data));
		}

		if (originalRequest._retry) return Promise.reject(new AuthError(errorMsg, status, data));

		originalRequest._retry = true;

		try {
			const token = await this.refreshTokenFlow();
			this.processQueue(token);
			return this.retryRequest(originalRequest, token);
		} catch (err) {
			forceLogOut({ message: "Please log in again" });
			return Promise.reject(err);
		}
	}

	private async refreshTokenFlow(): Promise<string> {
		if (this.isRefreshing) {
			return new Promise(resolve => this.failedQueue.push(resolve));
		}

		this.isRefreshing = true;

		try {
			const refreshToken = localStorage.getItem("refreshToken");
			if (!refreshToken) throw new AuthError("Missing refresh token", 401, null);

			let tokenData: TokenResponse;

			try {
				tokenData = await this.postRefreshAccessToken(refreshToken);
			} catch (e) {
				tokenData = await this.postRefreshRefreshToken();
			}

			return this.updateTokens(tokenData);
		} finally {
			this.isRefreshing = false;
		}
	}

	async postRefreshAccessToken(token: string) {
		const response = await fetch(this.baseURL + `/codeuser/auth/refresh_access_token`, {
			method: "POST",
			headers: {
				Authorization: `Bearer ${token}`,
				"Content-Type": "application/json",
			},
			body: JSON.stringify({}),
		});

		const responseJson: any = await response.json();
		if (!response.ok) {
			return Promise.reject(new Error(`HTTP error! status: ${response.status}`));
		}
		return responseJson;
	}

	private async postRefreshRefreshToken() {
		const refreshToken = localStorage.getItem("refreshToken");
		const response = await fetch(this.baseURL + `/codeuser/auth/refresh_refresh_token`, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
			},
			body: JSON.stringify(refreshToken),
		});

		const responseJson: any = await response.json();
		if (!response.ok) {
			return Promise.reject(new Error(`HTTP error! status: ${response.status}`));
		}
		return responseJson;
	}

	private updateTokens({ access_token, refresh_token }: TokenResponse): string {
		if (!access_token) throw new AuthError("Invalid token", 401, null);

		localStorage.setItem("accessToken", access_token);
		MsgCenterWeb.send("enterIDEAssistantAccessToken", { accesstoken: access_token });

		if (refresh_token) {
			localStorage.setItem("refreshToken", refresh_token);
			MsgCenterWeb.send("enterIDEAssistantRefreshToken", { refreshtoken: refresh_token });
		}

		return access_token;
	}

	private processQueue(token: string) {
		this.failedQueue.forEach(cb => cb(token));
		this.failedQueue = [];
	}

	private retryRequest(config: AxiosRequestConfig, token: string) {
		config.headers = { ...config.headers, Authorization: `Bearer ${token}` };
		return this.instance(config);
	}

	private getStatusMessage(status: number): string {
		const messages: Record<number, string> = {
			400: "Invalid request parameters",
			403: "Access denied",
			404: "Resource not found",
			500: "Internal server error",
			502: "Bad gateway",
			503: "Service unavailable",
			504: "Gateway timeout",
		};
		return messages[status] || `Request error (${status})`;
	}

	public get<T = any>(url: string, config?: AxiosRequestConfig): Promise<T> {
		return this.instance.get(url, config);
	}

	public post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
		return this.instance.post(url, data, config);
	}

	public put<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
		return this.instance.put(url, data, config);
	}

	public delete<T = any>(url: string, config?: AxiosRequestConfig): Promise<T> {
		return this.instance.delete(url, config);
	}
}

export const requestHttp = new RequestHttp();
