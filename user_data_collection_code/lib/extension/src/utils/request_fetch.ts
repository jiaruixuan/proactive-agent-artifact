import { Cookie, CookieJar } from "tough-cookie";
import { fetch, type ProxyAgent, type RequestInit as UndiciRequestInit } from "undici";
import * as vscode from "vscode";

import { ApiKeyManager } from "../ai/ApiKeyManager";
import { globalLogger } from "../extension";
import { getContext as getGlobalContext } from "../utils/GlobalVariables";
import { serverURL } from "../utils/ServerURL";
import { getBetterHttpErrorMessage } from "./betterHttpErrorMessage";


const cookieJar = new CookieJar();

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
	private apikeyManager?: ApiKeyManager;

	private async need_jwt(need_jwt?: boolean) {
		if (need_jwt) {
			this.apikeyManager = new ApiKeyManager({
				secretStorage: getGlobalContext()!.secrets,
			});
		}
	}

	private async fetchWithCookie(url: string, options: UndiciRequestInit = {}) {
		const cookies = await cookieJar.getCookies(url);
		if (cookies && cookies.length > 0) {
			(options.headers as Record<string, string>)["Cookie"] = cookies.map(cookie => cookie.cookieString()).join("; ");
		}

		const response = await fetch(url, options);

		const resCookie = Cookie.parse(response.headers.get("set-cookie") || "");
		if (resCookie) {
			cookieJar.setCookie(resCookie, url);
		}
		return response;
	}

	private async fetchWithAuthorization(url: string, options: UndiciRequestInit = {}, needJwt?: boolean) {
		options.headers = {
			...(options.headers as Record<string, string>),
		};

		if (needJwt && this.apikeyManager) {
			const token = await this.apikeyManager.getIDEAssistantAccessToken();
			if (token) {
				(options.headers as Record<string, string>)["Authorization"] = "Bearer " + token;
			}
		}

		const response = await this.fetchWithCookie(url, options);

		if (response.ok) {
			return response;
		}

		if (response.status === 401 && needJwt) {
			return this.handleUnauthorized(options, url);
		}

		const errorMessage = getBetterHttpErrorMessage(response.status);

		return Promise.reject(new Error(`HTTP error! status: ${response.status}`));
	}

	private async handleUnauthorized(options: UndiciRequestInit, url: string) {
		const authHeader = (options.headers as Record<string, string | undefined>)?.["Authorization"];
		if (typeof authHeader !== "string") return;

		const accessToken = authHeader.split(" ")[1];
		if (!accessToken || accessToken === "null") {
			globalLogger.appendLine(`handleUnauthorized -> access token not found -> logout`);
			await vscode.commands.executeCommand("ideassistant.tokenExpired", "Access token not found");
			return Promise.reject(new AuthError("Access token not found", 402, null));
		}

		try {
			const refreshToken = await this.apikeyManager?.getIDEAssistantRefreshToken();

			if (!refreshToken) throw new AuthError("Refresh token not found", 402, null);

			const newToken = (await this.refreshAccessToken(refreshToken)) as any;

			if (newToken.access_token) {
				await this.apikeyManager?.enterIDEAssistantAccessToken(newToken.access_token);
				(options.headers as Record<string, string>)["Authorization"] = "Bearer " + newToken.access_token;
				return await this.fetchWithCookie(url, options);
			} else return Promise.reject(new Error("Failed to refresh the access token."));
		} catch (error) {
			if (error instanceof AuthError) {
				return this.handleRefreshError(error, options, url);
			}
			return Promise.reject(error);
		}
	}

	private async handleRefreshError(error: any, options: UndiciRequestInit, url: string) {
		const refreshError = error as AuthError;
		const refreshToken = await this.apikeyManager?.getIDEAssistantRefreshToken();

		if (refreshToken && refreshError.status === 401) {
			let newTokens: any;
			try {
				newTokens = (await this.refreshRefreshToken(refreshToken)) as any;
			} catch (error) {
				if (error instanceof AuthError) {
					globalLogger.appendLine("handleRefreshError -> refresh refresh error: invalid refresh token -> logout");
					await vscode.commands.executeCommand("ideassistant.tokenExpired", "Invalid refresh token");
					return Promise.reject(new AuthError("Invalid refresh token", 403, null));
				}
				return Promise.reject(error);
			}

			if (newTokens.status_code === 2) {
				globalLogger.appendLine(`handleRefreshError -> refresh refresh error : ${newTokens.msg} -> logout`);
				await vscode.commands.executeCommand("ideassistant.tokenExpired", newTokens.msg);
				return Promise.reject(new AuthError(newTokens.msg, 403, null));
			}

			if (newTokens.access_token) {
				await this.apikeyManager?.enterIDEAssistantRefreshToken(newTokens.refresh_token);
				await this.apikeyManager?.enterIDEAssistantAccessToken(newTokens.access_token);
				(options.headers as Record<string, string>)["Authorization"] = "Bearer " + newTokens.access_token;
				return await this.fetchWithCookie(url, options);
			} else {
				return Promise.reject(new Error("Failed to refresh the access token."));
			}
		}

		if (refreshToken && refreshError.status === 404) {
			globalLogger.appendLine(`handleRefreshError -> refresh access error : user not found -> logout`);
			await vscode.commands.executeCommand("ideassistant.tokenExpired", "User not found");
			return Promise.reject(new AuthError("User not found", 404, null));
		}

		return Promise.reject(refreshError);
	}

	async refreshAccessToken(refreshToken: string) {
		const response = await this.fetchWithCookie(serverURL + `/codeuser/auth/refresh_access_token`, {
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
		const response = await this.fetchWithCookie(serverURL + `/codeuser/auth/refresh_refresh_token`, {
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
		await this.need_jwt(needJwt);
		return this.fetchWithAuthorization(url, { method: "GET" }, needJwt);
	}

	async post(
		url: string,
		body?: object,
		buffer_body?: Buffer,
		need_jwt?: boolean,
		signal?: AbortSignal,
		proxyClient?: ProxyAgent,
		customHeaders?: object
	): Promise<any> {
		await this.need_jwt(need_jwt);
		const headers = {
			...customHeaders,
		};

		const options: UndiciRequestInit = {
			method: "POST",
			body: buffer_body || (body ? JSON.stringify(body) : undefined),
			headers: headers,
		};
		if (signal) {
			options.signal = signal;
		}
		if (proxyClient) {
			options.dispatcher = proxyClient;
		}

		return this.fetchWithAuthorization(url, options, need_jwt);
	}

	async put(url: string, body?: object, need_jwt?: boolean): Promise<any> {
		await this.need_jwt(need_jwt);
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
		await this.need_jwt(need_jwt);
		return this.fetchWithAuthorization(
			url,
			{
				method: "DELETE",
				body: JSON.stringify(body),
			},
			need_jwt
		);
	}
}

const requestFetch = new RequestHttp();
export default requestFetch;
