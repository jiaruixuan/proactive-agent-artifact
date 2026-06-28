import * as Sentry from "@sentry/react";
import { MsgCenterWeb } from "@/messageCenter/msgCenterWeb";
import { Env } from "@/utils/getEnv";
import { parseUrlParams } from "@/utils/util";
import { globalConfig } from "../globalConfig";
import { forceLogOut } from ".";

let baseURL = Env.isDev ? "" : globalConfig.APP_API_URL;

const isEnterprise = globalConfig.APP_VERSION === "enterprise";
if (isEnterprise) {
	const links = document.querySelectorAll("link");
	if (links.length > 0) {
		const baseURI = parseUrlParams(links[0].baseURI);
		baseURL = baseURI.serverUrl;
	}
}

export class AuthError extends Error {
	status: number;
	data: any;

	constructor(message: string, status: number, data: any) {
		super(message);
		this.status = status;
		this.data = data;
		this.name = "AuthError";
	}
}

class RequestHttp {
	private async fetchWithAuthorization(url: string, options: RequestInit = {}, needJwt?: boolean) {
		options.headers = {
			...(options.headers as Record<string, string>),
		};

		if (needJwt) {
			const accessToken = localStorage.getItem("accessToken");
			if (accessToken) {
				(options.headers as Record<string, string>)["Authorization"] = "Bearer " + accessToken;
			}
		}

		const response = await fetch((url.startsWith("http") ? "" : baseURL) + url, options);
		if (response.ok) {
			return response;
		}

		if (response.status === 401 && needJwt) {
			return this.handleUnauthorized(options, url);
		}

		const userId = localStorage.getItem("userId");
		const data = localStorage.getItem("data");
		let selectedId = "";
		if (data) {
			const dataArray = JSON.parse(data);
			selectedId = dataArray[0].selectedConversationId;
		} else {
			selectedId = "";
		}

		Sentry.captureException(new Error(`HTTP error! Status: ${response.status}`), {
			level: "error",
			tags: {
				http_status: response.status.toString(),
				api_endpoint: url,
				need_jwt: needJwt ? "true" : "false",
				request_method: options.method || "GET",
			},
			extra: {
				response_status: response.status,
				userId: userId,
				request_url: baseURL + url,
				conversationId: selectedId,
			},
		});

		return Promise.reject(new Error(`HTTP error! status: ${response.status}`));
	}

	private async handleUnauthorized(options: RequestInit, url: string) {
		const authHeader = (options.headers as Record<string, string | undefined>)?.["Authorization"];
		if (typeof authHeader !== "string") {
			forceLogOut({ message: "Access token not found" });
			return Promise.reject(new AuthError("Access token not found", 402, null));
		}

		const accessToken = authHeader.split(" ")[1];
		if (!accessToken || accessToken === "null") {
			forceLogOut({ message: "Access token not found" });
			return Promise.reject(new AuthError("Access token not found", 402, null));
		}

		try {
			const refreshToken = localStorage.getItem("refreshToken");

			if (!refreshToken) {
				forceLogOut({ message: "Refresh token not found" });
				return Promise.reject(new AuthError("Refresh token not found.", 402, null));
			}

			const newToken = await this.refreshAccessToken(refreshToken);

			if (newToken.access_token) {
				MsgCenterWeb.send("enterIDEAssistantAccessToken", { accesstoken: newToken.access_token });
				localStorage.setItem("accessToken", newToken.access_token);
				(options.headers as Record<string, string>)["Authorization"] = "Bearer " + newToken.access_token;
				return await fetch(baseURL + url, options);
			} else return Promise.reject(new Error("Failed to refresh the access token."));
		} catch (error) {
			if (error instanceof AuthError) {
				return this.handleRefreshError(error, options, url);
			}
			return Promise.reject(error);
		}
	}

	private async handleRefreshError(error: any, options: RequestInit, url: string) {
		const refreshError = error instanceof AuthError ? error : new AuthError("Unknown error", 500, error);
		const refreshToken = localStorage.getItem("refreshToken");

		if (refreshToken && refreshError.status === 401) {
			let newTokens: any;
			try {
				newTokens = await this.refreshRefreshToken(refreshToken);
			} catch (error) {
				if (error instanceof AuthError) {
					forceLogOut({ message: "Invalid refresh token" });
					return Promise.reject(new AuthError("Invalid refresh token", 403, null));
				}
				return Promise.reject(error);
			}

			if (newTokens.status_code === 2) {
				forceLogOut({ message: newTokens.msg });
				return Promise.reject(new AuthError(newTokens.msg!, 403, null));
			}

			if (newTokens.access_token) {
				MsgCenterWeb.send("enterIDEAssistantAccessToken", { accesstoken: newTokens.access_token });
				MsgCenterWeb.send("enterIDEAssistantRefreshToken", { refreshtoken: newTokens.refresh_token });
				localStorage.setItem("refreshToken", newTokens.refresh_token!);
				localStorage.setItem("accessToken", newTokens.access_token);
				(options.headers as Record<string, string>)["Authorization"] = "Bearer " + newTokens.access_token;
				return await fetch(baseURL + url, options);
			} else {
				return Promise.reject(new Error("Failed to refresh the access token."));
			}
		}

		if (refreshToken && refreshError.status === 404) {
			forceLogOut({ message: "User not found" });
			return Promise.reject(new AuthError("User not found", 404, null));
		}

		return Promise.reject(refreshError);
	}

	async refreshAccessToken(refreshToken: string) {
		const response = await fetch(baseURL + `/codeuser/auth/refresh_access_token`, {
			method: "POST",
			headers: {
				Authorization: `Bearer ${refreshToken}`,
				"Content-Type": "application/json",
			},
			body: JSON.stringify({}),
		});

		const responseJson: any = await response.json();
		if (!response.ok) {
			if ((response.status === 404 && responseJson.detail === "User not found") || response.status === 401) {
				return Promise.reject(new AuthError(`HTTP error! status: ${response.status}`, response.status, null));
			}
			return Promise.reject(new Error(`HTTP error! status: ${response.status}`));
		}

		return responseJson;
	}

	async refreshRefreshToken(token: string) {
		const response = await fetch(baseURL + `/codeuser/auth/refresh_refresh_token`, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
			},
			body: JSON.stringify(token),
		});

		const responseJson: any = await response.json();
		if (!response.ok) {
			if (response.status === 401 && responseJson.detail === "Invalid refresh token") {
				return Promise.reject(new AuthError(`HTTP error! status: ${response.status}`, response.status, null));
			}
			return Promise.reject(new Error(`HTTP error! status: ${response.status}`));
		}

		return responseJson;
	}

	async get(url: string, needJwt?: boolean): Promise<any> {
		return this.fetchWithAuthorization(url, { method: "GET" }, needJwt);
	}

	async post(
		url: string,
		body?: object,
		buffer_body?: Buffer,
		need_jwt?: boolean,
		signal?: AbortSignal,
		customHeaders?: object
	): Promise<any> {
		const headers = {
			...customHeaders,
		};
		const options: RequestInit = {
			method: "POST",
			body: buffer_body ? new Uint8Array(buffer_body) : body ? JSON.stringify(body) : undefined,
			headers: headers,
		};
		if (signal) {
			options.signal = signal;
		}

		return this.fetchWithAuthorization(url, options, need_jwt);
	}

	async put(url: string, body?: object, need_jwt?: boolean): Promise<any> {
		return this.fetchWithAuthorization(
			url,
			{
				method: "PUT",
				body: JSON.stringify(body),
			},
			need_jwt
		);
	}

	async delete(url: string, body?: object, need_jwt?: boolean): Promise<any> {
		return this.fetchWithAuthorization(
			url,
			{
				method: "DELETE",
				body: JSON.stringify(body),
			},
			need_jwt
		);
	}
	async _fetch(url: string, body?: RequestInit, needJwt = false): Promise<any> {
		const options: RequestInit = body || {};
		return this.fetchWithAuthorization(url, options, needJwt);
	}
}

const oldFetch = new RequestHttp();
export default oldFetch;
